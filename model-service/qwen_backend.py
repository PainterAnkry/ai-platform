"""Real local model backend for the model service (Qwen3.5-0.8B).

The default model service is a deterministic *stub*: it answers by extracting
sentences and hashing text into vectors, so the whole Provider chain can be
demoed without any download or API key. This module adds the other half: it
loads a real Hugging Face-format checkpoint with ``transformers`` and generates
text behind exactly the same OpenAI-compatible HTTP surface, so the platform's
``LLM_PROVIDER=openai`` path talks to genuine weights without a code change.

Only the chat endpoint uses this backend; ``/v1/embeddings`` and ``/v1/rerank``
keep the deterministic implementations (hosting a real embedding model is a
separate, equally mechanical step).

Environment (all optional except the path):

============================  =================================================
``QWEN_MODEL_PATH``           local checkpoint directory; the backend is only
                              active when ``MODEL_SERVICE_LLM_BACKEND=qwen``
``QWEN_DEVICE``               ``auto`` | ``cpu`` | ``cuda`` (default ``auto``)
``QWEN_DTYPE``                ``auto`` | ``float32`` | ``bfloat16`` | ``float16``
``QWEN_MAX_NEW_TOKENS``       default 512
``QWEN_THREADS``              torch CPU threads; 0 keeps the torch default
``QWEN_ENABLE_THINKING``      ``false`` (default) asks the chat template to skip
                              the reasoning block; ``true`` keeps it and exposes
                              it as ``reasoning_content``
``QWEN_TEMPERATURE``          default 0.7 (0 = greedy); the caller's request
                              temperature wins, so set ``LLM_TEMPERATURE=0.7``
                              in the platform's .env too
``QWEN_TOP_P``                default 0.9
``QWEN_TOP_K``                default 20 (Qwen's recommended non-thinking value)
``QWEN_RETRY_DEGENERATE``     default 2: regenerate when an answer carries no
                              body (empty or a bare citation line)
``QWEN_STREAM_HOLD_CHARS``    default 40: characters buffered before streaming
                              starts, so a degenerate answer can be retried;
                              0 streams raw tokens with no guard
============================  =================================================

Load once, generate many: the checkpoint is loaded lazily on first use and
cached for the process lifetime (thread-safe). CPU inference of a 0.8B model is
usable for demos; a CUDA build of torch makes it fast, and this module picks it
up automatically when ``QWEN_DEVICE=auto`` and ``torch.cuda.is_available()``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from typing import Any, Iterable, Iterator, Sequence

logger = logging.getLogger("model-service.qwen")

DEFAULT_MAX_NEW_TOKENS = 512
# Safety valve so a stream can never be blocked forever by an unterminated
# reasoning block.
_THINK_HOLD_LIMIT = 4000

_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)
_FUNCTION_RE = re.compile(r"<function=([^>\s]+)\s*>(.*?)</function>", re.S)
_PARAMETER_RE = re.compile(r"<parameter=([^>\s]+)\s*>\s*(.*?)\s*</parameter>", re.S)
_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"

# A 0.8B model occasionally short-circuits on the RAG prompt and emits only the
# citation line the instruction asked for ("来源：【片段 1】") instead of an
# answer. Measured on this machine: 1 of 3 samples usable at temperature 0.2,
# 2 of 3 at 0.7-1.0, and a retry at the same temperature tends to reproduce the
# shortcut. So retries escalate the temperature, and a degenerate answer costs
# almost nothing to redo (it is about 10 tokens long).
_DEGENERATE_MAX_CHARS = 40
_RETRY_TEMPERATURE_FLOOR = (0.7, 1.0)
_CITATION_MARKERS = ("来源", "Source", "source:")


class QwenUnavailable(RuntimeError):
    """Raised when the real-model backend cannot serve a request."""


# ---------------------------------------------------------------------------
# settings (read at call time; the service is configured through the env)
# ---------------------------------------------------------------------------
def model_path() -> str:
    return os.getenv("QWEN_MODEL_PATH", "").strip()


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def enable_thinking() -> bool:
    return _env_flag("QWEN_ENABLE_THINKING", False)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _default_max_new_tokens() -> int:
    return _int_env("QWEN_MAX_NEW_TOKENS", DEFAULT_MAX_NEW_TOKENS)


# ---------------------------------------------------------------------------
# model loading
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_state: dict[str, Any] = {}


def is_loaded() -> bool:
    return bool(_state.get("model"))


def load() -> dict[str, Any]:
    """Load tokenizer + model once and cache them (thread-safe)."""
    if _state.get("model") is not None:
        return _state

    with _lock:
        if _state.get("model") is not None:
            return _state

        path = model_path()
        if not path:
            raise QwenUnavailable(
                "QWEN_MODEL_PATH is not set; point it at the local checkpoint "
                "directory (or unset MODEL_SERVICE_LLM_BACKEND to use the stub)"
            )
        if not os.path.isdir(path):
            raise QwenUnavailable(f"QWEN_MODEL_PATH does not exist: {path}")

        try:
            import torch
            from transformers import AutoTokenizer
        except ImportError as exc:  # pragma: no cover - depends on install
            raise QwenUnavailable(
                "the real-model backend needs torch + transformers; install "
                "model-service/requirements-qwen.txt into this environment"
            ) from exc

        threads = _int_env("QWEN_THREADS", 0)
        if threads > 0:
            torch.set_num_threads(threads)

        device = _resolve_device(torch)
        dtype = _resolve_dtype(torch, device)
        logger.info(
            "loading local model from %s (device=%s, dtype=%s)", path, device, dtype
        )

        started = time.perf_counter()
        tokenizer = _load_tokenizer(AutoTokenizer, path)
        processor = _load_processor(path)
        model, load_errors = _load_model(path, device, dtype)
        if model is None:
            raise QwenUnavailable(
                "could not load the checkpoint with any supported auto class: "
                + " | ".join(load_errors)
            )
        model.eval()

        _state.update(
            {
                "model": model,
                "tokenizer": tokenizer,
                "processor": processor,
                "device": device,
                "dtype": str(dtype).replace("torch.", ""),
                "load_seconds": round(time.perf_counter() - started, 2),
                "supports_thinking": "enable_thinking" in (tokenizer.chat_template or ""),
            }
        )
        logger.info(
            "model ready in %.1fs (dtype=%s, thinking switch=%s, vision=%s)",
            _state["load_seconds"],
            _state["dtype"],
            _state["supports_thinking"],
            processor is not None,
        )
        return _state


def _resolve_device(torch: Any) -> str:
    requested = os.getenv("QWEN_DEVICE", "auto").strip().lower()
    if requested in ("cpu", "cuda"):
        return requested
    return "cuda" if torch.cuda.is_available() else "cpu"


def _resolve_dtype(torch: Any, device: str) -> Any:
    requested = os.getenv("QWEN_DTYPE", "auto").strip().lower()
    mapping = {
        "float32": torch.float32,
        "fp32": torch.float32,
        "bfloat16": torch.bfloat16,
        "bf16": torch.bfloat16,
        "float16": torch.float16,
        "fp16": torch.float16,
    }
    if requested in mapping:
        return mapping[requested]
    if device == "cuda":
        return torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    # CPU: float32 is the dependable choice. bfloat16 on CPU is emulated and
    # measured about 8x slower here (0.54 vs 4.5 tok/s); a 0.8B checkpoint is
    # roughly 3.5 GB in fp32, which fits comfortably in 16 GB.
    return torch.float32


def _load_tokenizer(auto_tokenizer: Any, path: str) -> Any:
    try:
        tokenizer = auto_tokenizer.from_pretrained(path)
    except Exception:  # noqa: BLE001 - VL repos sometimes need the processor
        from transformers import AutoProcessor

        processor = AutoProcessor.from_pretrained(path)
        tokenizer = getattr(processor, "tokenizer", processor)
    if tokenizer.pad_token_id is None and tokenizer.eos_token_id is not None:
        tokenizer.pad_token_id = tokenizer.eos_token_id
    return tokenizer


def _load_processor(path: str) -> Any:
    """Best-effort processor for vision input (None on text-only installs)."""
    try:
        from transformers import AutoProcessor

        return AutoProcessor.from_pretrained(path)
    except Exception as exc:  # noqa: BLE001 - vision stays optional
        logger.info("no multimodal processor available (%s); vision disabled", exc)
        return None


def _load_model(path: str, device: str, dtype: Any) -> tuple[Any, list[str]]:
    """Try the auto classes that can host this checkpoint, in order.

    ``AutoModelForImageTextToText`` goes first: multimodal checkpoints
    (``Qwen3_5ForConditionalGeneration``) load their vision tower that way,
    while a text-only config simply fails here and falls through to
    ``AutoModelForCausalLM``.
    """
    import transformers

    candidates = [
        name
        for name in (
            "AutoModelForImageTextToText",
            "AutoModelForCausalLM",
            "AutoModelForMultimodalLM",
            "AutoModelForVision2Seq",
        )
        if hasattr(transformers, name)
    ]
    errors: list[str] = []
    for name in candidates:
        auto_class = getattr(transformers, name)
        try:
            model = auto_class.from_pretrained(path, dtype=dtype)
            return model.to(device), errors
        except Exception as exc:  # noqa: BLE001 - try the next auto class
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            logger.debug("auto class %s rejected the checkpoint: %s", name, exc)
    return None, errors


# ---------------------------------------------------------------------------
# prompt / output handling
# ---------------------------------------------------------------------------
def _collect_images(messages: Sequence[dict[str, Any]]) -> list[Any]:
    """Extract PIL images from multimodal message contents.

    Accepts both the OpenAI wire form (``{"type": "image_url", "image_url":
    {"url": "data:image/png;base64,..."}}``) and the HF template form
    (``{"type": "image", "image": <PIL image | local path>}``).
    """
    import base64
    from io import BytesIO

    from PIL import Image

    images: list[Any] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "image_url":
                url = str((item.get("image_url") or {}).get("url") or "")
                if url.startswith("data:"):
                    payload = url.partition("base64,")[2]
                    try:
                        images.append(Image.open(BytesIO(base64.b64decode(payload))))
                    except Exception as exc:  # noqa: BLE001 - reported to caller
                        raise QwenUnavailable(f"无法解码图片: {exc}") from exc
            elif item.get("type") == "image" and item.get("image") is not None:
                image = item["image"]
                if isinstance(image, str):
                    images.append(Image.open(image))
                else:
                    images.append(image)
    return images


def _to_template_messages(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise OpenAI multimodal contents into the HF template form."""
    out: list[dict[str, Any]] = []
    image_index = 0
    for message in messages:
        entry = dict(message)
        content = entry.get("content")
        if isinstance(content, list):
            normalised: list[dict[str, Any]] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "image_url":
                    normalised.append(
                        {"type": "image", "image": f"__image_{image_index}__"}
                    )
                    image_index += 1
                else:
                    normalised.append({"type": "text", "text": str(item.get("text") or "")})
            entry["content"] = normalised
        out.append(entry)
    return out


def build_prompt(
    messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]] | None
) -> str:
    """Render the chat template for ``messages`` (and tool schemas)."""
    state = load()
    tokenizer = state["tokenizer"]
    kwargs: dict[str, Any] = {"tokenize": False, "add_generation_prompt": True}
    if tools:
        kwargs["tools"] = list(tools)
    if state.get("supports_thinking"):
        kwargs["enable_thinking"] = enable_thinking()
    try:
        return tokenizer.apply_chat_template(_to_template_messages(messages), **kwargs)
    except TypeError:
        # Older template without the thinking switch: retry without it.
        kwargs.pop("enable_thinking", None)
        return tokenizer.apply_chat_template(_to_template_messages(messages), **kwargs)


def split_thinking(text: str) -> tuple[str, str]:
    """Split a completion into ``(answer, reasoning)``.

    Qwen3.5 wraps reasoning in think tags. When the model was asked not to think
    there is nothing to split, and when it was cut off mid-thought the reasoning
    is returned with an empty answer rather than leaking half a thought into the
    answer.
    """
    if _THINK_CLOSE in text:
        head, _, tail = text.rpartition(_THINK_CLOSE)
        reasoning = head.split(_THINK_OPEN)[-1].strip()
        return tail.strip(), reasoning
    if _THINK_OPEN in text:
        return "", text.split(_THINK_OPEN)[-1].strip()
    return text.strip(), ""


def parse_tool_calls(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Extract Qwen's XML tool calls into the OpenAI ``tool_calls`` shape.

    The chat template teaches the model to answer with::

        <tool_call>
        <function=knowledge_search>
        <parameter=query>refund policy</parameter>
        </function>
        </tool_call>

    which is *not* the JSON API the platform's agent loop consumes. This
    translation is what lets a real local model drive the Phase 5 tools.
    """
    calls: list[dict[str, Any]] = []
    for block in _TOOL_CALL_RE.findall(text):
        for name, body in _FUNCTION_RE.findall(block):
            arguments: dict[str, Any] = {}
            for key, raw in _PARAMETER_RE.findall(body):
                value = raw.strip()
                try:
                    arguments[key] = json.loads(value)
                except (json.JSONDecodeError, ValueError):
                    arguments[key] = value
            calls.append(
                {
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                }
            )
    stripped = _TOOL_CALL_RE.sub("", text).strip()
    return stripped, calls


class ThinkingFilter:
    """Holds back streamed text until the reasoning block has closed.

    With ``QWEN_ENABLE_THINKING=false`` nothing is withheld. With thinking on,
    emitting the raw stream would leak the model's private reasoning into the
    chat answer, so chunks are buffered until the closing tag appears.
    """

    def __init__(self, hold_back: bool) -> None:
        self._hold = hold_back
        self._buffer = ""
        self._done = not hold_back

    def feed(self, chunk: str) -> str:
        if self._done:
            return chunk
        self._buffer += chunk
        if _THINK_CLOSE in self._buffer:
            answer = self._buffer.rpartition(_THINK_CLOSE)[2]
            self._buffer, self._done = "", True
            return answer.lstrip("\n")
        if len(self._buffer) > _THINK_HOLD_LIMIT:
            # The model never closed its reasoning block; stop hiding output.
            self._done = True
            return self._buffer
        return ""

    def flush(self) -> str:
        leftover, self._buffer = self._buffer, ""
        return leftover


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------
def _generation_kwargs(
    max_tokens: int | None, temperature: float | None
) -> dict[str, Any]:
    temp = _float_env("QWEN_TEMPERATURE", 0.7) if temperature is None else temperature
    kwargs: dict[str, Any] = {
        "max_new_tokens": max_tokens or _default_max_new_tokens(),
        "repetition_penalty": 1.05,
    }
    if temp and temp > 0:
        kwargs.update(
            do_sample=True,
            temperature=temp,
            top_p=_float_env("QWEN_TOP_P", 0.9),
            # top_k is what keeps this small model on the rails: measured on the
            # RAG prompt, 0/3 usable samples without it (any temperature) versus
            # 3/3 at temperature 0.7 with Qwen's recommended top_k=20.
            top_k=_int_env("QWEN_TOP_K", 20),
        )
    else:
        kwargs.update(do_sample=False)
    return kwargs


def _looks_degenerate(text: str) -> bool:
    """True when a completion carries no answer body (empty or citation-only)."""
    stripped = text.strip()
    if not stripped:
        return True
    if len(stripped) >= _DEGENERATE_MAX_CHARS:
        return False
    return any(marker in stripped for marker in _CITATION_MARKERS)


def _attempt_temperature(base: float | None, attempt: int) -> float | None:
    """Sampling temperature for retry ``attempt`` (0 = the caller's request)."""
    if attempt <= 0:
        return base
    index = min(attempt - 1, len(_RETRY_TEMPERATURE_FLOOR) - 1)
    floor = _RETRY_TEMPERATURE_FLOOR[index]
    return floor if base is None else max(base, floor)


def _prepare_inputs(prompt: str, images: Sequence[Any] | None = None) -> tuple[Any, Any, int]:
    import torch

    state = load()
    if images and state.get("processor") is not None:
        encoded = state["processor"](
            text=[prompt], images=list(images), return_tensors="pt"
        )
        prompt_tokens = int(encoded["input_ids"].shape[1])
        return torch, encoded.to(state["device"]), prompt_tokens
    tokenizer, device = state["tokenizer"], state["device"]
    encoded = tokenizer(prompt, return_tensors="pt")
    prompt_tokens = int(encoded["input_ids"].shape[1])
    return torch, encoded.to(device), prompt_tokens


def complete(
    messages: Sequence[dict[str, Any]],
    *,
    tools: Sequence[dict[str, Any]] | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    """One non-streaming completion, retrying a degenerate answer."""
    prompt = build_prompt(messages, tools)
    images = _collect_images(messages)
    retries = max(0, _int_env("QWEN_RETRY_DEGENERATE", 2))
    result: dict[str, Any] = {}
    for attempt in range(retries + 1):
        result = _complete_once(
            prompt, images, max_tokens, _attempt_temperature(temperature, attempt)
        )
        if not _looks_degenerate(result["content"]) or attempt >= retries:
            break
        logger.info(
            "degenerate local answer %r; retrying (%d/%d)",
            result["content"][:60],
            attempt + 1,
            retries,
        )
    return result


def _complete_once(
    prompt: str,
    images: Sequence[Any] | None,
    max_tokens: int | None,
    temperature: float | None,
) -> dict[str, Any]:
    torch, inputs, prompt_tokens = _prepare_inputs(prompt, images)
    state = load()
    started = time.perf_counter()
    with torch.inference_mode():
        output = state["model"].generate(
            **inputs, **_generation_kwargs(max_tokens, temperature)
        )
    generated = output[0][prompt_tokens:]
    raw = state["tokenizer"].decode(generated, skip_special_tokens=True)
    elapsed = time.perf_counter() - started

    answer, reasoning = split_thinking(raw)
    content, tool_calls = parse_tool_calls(answer)
    completion_tokens = int(generated.shape[0])
    return {
        "content": content,
        "reasoning": reasoning,
        "tool_calls": tool_calls,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "elapsed": round(elapsed, 3),
        "tokens_per_second": round(completion_tokens / elapsed, 2)
        if elapsed > 0
        else 0.0,
        "dtype": state.get("dtype"),
        "device": state.get("device"),
    }


def stream(
    messages: Sequence[dict[str, Any]],
    *,
    tools: Sequence[dict[str, Any]] | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> Iterator[str]:
    """Yield answer deltas, with a degenerate-answer guard.

    Delivering tokens as they are produced is the point of streaming, but a
    degenerate answer is only recognisable once it is complete. So the first
    ``QWEN_STREAM_HOLD_CHARS`` characters (default 40) are buffered: if the
    stream ends while still buffered and the text carries no answer body, the
    generation is repeated (hotter) instead of shown. Set it to 0 to stream raw
    tokens with no guard.
    """
    prompt = build_prompt(messages, tools)
    images = _collect_images(messages)
    hold_chars = max(0, _int_env("QWEN_STREAM_HOLD_CHARS", 40))
    retries = max(0, _int_env("QWEN_RETRY_DEGENERATE", 2))

    for attempt in range(retries + 1):
        held: list[str] = []
        delivered = False
        for delta in _stream_once(
            prompt, images, max_tokens, _attempt_temperature(temperature, attempt)
        ):
            if delivered:
                yield delta
                continue
            held.append(delta)
            if hold_chars == 0 or len("".join(held)) >= hold_chars:
                delivered = True
                yield "".join(held)
        if delivered:
            return
        text = "".join(held)
        if attempt < retries and _looks_degenerate(text):
            logger.info(
                "degenerate local stream %r; regenerating (%d/%d)",
                text[:60],
                attempt + 1,
                retries,
            )
            continue
        if text:
            yield text
        return


def _stream_once(
    prompt: str,
    images: Sequence[Any] | None,
    max_tokens: int | None,
    temperature: float | None,
) -> Iterator[str]:
    from transformers import TextIteratorStreamer

    state = load()
    tokenizer = state["tokenizer"]
    torch, inputs, _ = _prepare_inputs(prompt, images)

    streamer = TextIteratorStreamer(
        tokenizer, skip_prompt=True, skip_special_tokens=True, timeout=300.0
    )
    kwargs = dict(
        inputs, streamer=streamer, **_generation_kwargs(max_tokens, temperature)
    )
    thread = threading.Thread(
        target=state["model"].generate, kwargs=kwargs, name="qwen-generate", daemon=True
    )
    thread.start()

    filters = ThinkingFilter(hold_back=enable_thinking())
    try:
        for chunk in streamer:
            if not chunk:
                continue
            visible = filters.feed(chunk)
            if visible:
                yield visible
    finally:
        thread.join(timeout=5.0)
    leftover = filters.flush()
    if leftover:
        yield leftover


def describe() -> dict[str, Any]:
    """Cheap status payload for /health (never loads the model)."""
    return {
        "backend": "qwen",
        "model_path": model_path(),
        "loaded": is_loaded(),
        "device": _state.get("device"),
        "dtype": _state.get("dtype"),
        "load_seconds": _state.get("load_seconds"),
        "enable_thinking": enable_thinking(),
        "max_new_tokens": _default_max_new_tokens(),
        "retry_degenerate": _int_env("QWEN_RETRY_DEGENERATE", 2),
    }


# ---------------------------------------------------------------------------
# tiny CLI for smoke tests: python qwen_backend.py "question" [--stream]
# ---------------------------------------------------------------------------
def _cli(argv: Iterable[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Query the local model directly.")
    parser.add_argument("question", nargs="?", default="用一句话介绍你自己。")
    parser.add_argument("--system", default="")
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--max-tokens", type=int, default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    messages: list[dict[str, Any]] = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    messages.append({"role": "user", "content": args.question})

    if args.stream:
        started = time.perf_counter()
        chunks = 0
        for delta in stream(messages, max_tokens=args.max_tokens):
            chunks += 1
            print(delta, end="", flush=True)
        print(f"\n--- {chunks} delta(s) in {time.perf_counter() - started:.2f}s")
        return 0

    result = complete(messages, max_tokens=args.max_tokens)
    print(result["content"])
    if result["reasoning"]:
        print(f"\n[reasoning] {result['reasoning'][:400]}")
    print(
        f"\n--- {result['completion_tokens']} tokens in {result['elapsed']}s "
        f"({result['tokens_per_second']} tok/s, {result['device']}, {result['dtype']})"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    raise SystemExit(_cli())
