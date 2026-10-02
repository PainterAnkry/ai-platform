"""Self-hosted model service: OpenAI-compatible, stub or real weights.

Implements the three AI endpoints the platform's providers consume:

* POST /v1/embeddings        — hashed char-n-gram vectors (unit length)
* POST /v1/rerank            — lexical overlap scoring (Jina/Cohere shape)
* POST /v1/chat/completions  — answers, streaming and not

Two LLM backends are selectable at runtime through ``MODEL_SERVICE_LLM_BACKEND``:

``stub`` (default)
    Deterministic extractive answers, no downloads, no GPU. This is what keeps
    the full Provider chain (embedding -> reranker -> LLM, all over real HTTP)
    demoable offline, and what the contract tests pin.

``qwen``
    Real local generation from a Hugging Face-format checkpoint (verified with
    Qwen/Qwen3.5-0.8B) through ``qwen_backend.py``: chat template, optional
    reasoning block, SSE deltas, and OpenAI ``tool_calls`` translated from
    Qwen's XML tool syntax. Requires ``QWEN_MODEL_PATH`` plus torch and
    transformers (``requirements-qwen.txt``).

Embeddings and reranking always use the deterministic implementations; making
them real is a separate step (``/v1/embeddings`` would host e.g. bge-m3).

NOTE: no ``from __future__ import annotations`` here on purpose — pydantic
resolves the model annotations eagerly, which keeps the module loadable via
importlib (the backend contract tests do exactly that).
"""

import hashlib
import json
import logging
import math
import os
import time
import uuid
from typing import Any, Optional, Union

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("model-service")

DIM = int(os.getenv("MODEL_SERVICE_DIM", "1024"))
VERSION = "1.1.0"

# "stub" keeps the offline path; "qwen" loads a local checkpoint.
LLM_BACKEND = os.getenv("MODEL_SERVICE_LLM_BACKEND", "stub").strip().lower()

app = FastAPI(title="ai-platform model-service", version=VERSION)


# ---------------------------------------------------------------------------
# real-model backend (loaded lazily and by path, so importing this module never
# requires torch — the stub deployment stays dependency-free)
# ---------------------------------------------------------------------------
_QWEN_MODULE: Any = None


def _qwen_module():
    """Import ``qwen_backend.py`` from this file's directory — once.

    The module holds the loaded checkpoint, so re-executing it per request
    would reload the weights on every call (measured: a full model load per
    HTTP request, and again per retry).
    """
    global _QWEN_MODULE
    if _QWEN_MODULE is not None:
        return _QWEN_MODULE

    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().with_name("qwen_backend.py")
    if not path.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"MODEL_SERVICE_LLM_BACKEND=qwen but {path.name} is missing",
        )
    spec = importlib.util.spec_from_file_location("model_service_qwen_backend", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail="cannot load qwen_backend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _QWEN_MODULE = module
    return module


def _qwen_or_503():
    backend = _qwen_module()
    try:
        backend.load()
    except backend.QwenUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return backend


def _normalise_tool_call(call: dict[str, Any]) -> dict[str, Any]:
    """Reshape one OpenAI tool call for the chat template.

    The wire format carries ``function.arguments`` as a JSON *string*, while
    chat templates iterate it as a mapping (``tool_call.arguments | items``).
    Passing the string through makes Jinja raise
    ``TypeError: Can only get item pairs from a mapping`` — which is exactly how
    an agent's second turn (the one that replays its own tool call) failed.
    """
    normalised = dict(call)
    function = dict(normalised.get("function") or {})
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        try:
            function["arguments"] = json.loads(arguments) if arguments.strip() else {}
        except ValueError:
            function["arguments"] = {}
    elif not isinstance(arguments, dict):
        function["arguments"] = {}
    normalised["function"] = function
    return normalised


def _messages_for_template(messages: list["ChatMessageIn"]) -> list[dict[str, Any]]:
    """Convert request messages into the dict shape chat templates expect."""
    out: list[dict[str, Any]] = []
    for message in messages:
        entry: dict[str, Any] = {"role": message.role, "content": message.content or ""}
        if message.tool_calls:
            entry["tool_calls"] = [_normalise_tool_call(call) for call in message.tool_calls]
        if message.tool_call_id:
            entry["tool_call_id"] = message.tool_call_id
        if message.name:
            entry["name"] = message.name
        out.append(entry)
    return out


def _chat_completion_payload(
    payload: "ChatRequest", result: dict[str, Any]
) -> dict[str, Any]:
    """Wrap a backend result in the OpenAI chat-completion envelope."""
    message: dict[str, Any] = {
        "role": "assistant",
        "content": result.get("content") or "",
    }
    if result.get("tool_calls"):
        message["tool_calls"] = result["tool_calls"]
    if result.get("reasoning"):
        # Not part of the OpenAI schema, but harmless: clients ignore unknown
        # fields, and it keeps the model's reasoning inspectable.
        message["reasoning_content"] = result["reasoning"]
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": payload.model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if result.get("tool_calls") else "stop",
            }
        ],
        "usage": {
            "prompt_tokens": result.get("prompt_tokens", 0),
            "completion_tokens": result.get("completion_tokens", 0),
            "total_tokens": result.get("prompt_tokens", 0)
            + result.get("completion_tokens", 0),
        },
    }


def _qwen_chat_stream(payload: "ChatRequest"):
    """SSE stream of real model deltas in OpenAI's chunk format."""
    from fastapi.responses import StreamingResponse

    backend = _qwen_or_503()

    def frame(delta: dict[str, Any], finish: Optional[str]) -> str:
        chunk = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": payload.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    def gen():
        yield frame({"role": "assistant", "content": ""}, None)
        try:
            for delta in backend.stream(
                _messages_for_template(payload.messages),
                tools=payload.tools,
                max_tokens=payload.max_tokens,
                temperature=payload.temperature,
            ):
                yield frame({"content": delta}, None)
        except Exception as exc:  # noqa: BLE001 - surface as an SSE error
            logger.exception("qwen stream failed")
            yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return
        yield frame({}, "stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# shared helpers (mirrors app/rag algorithms, deliberately self-contained)
# ---------------------------------------------------------------------------
def _ngrams(text: str) -> list[str]:
    normalized = " ".join(text.lower().split())
    grams: list[str] = []
    for size in (2, 3):
        if len(normalized) < size:
            grams.append(normalized)
            continue
        grams.extend(
            normalized[i : i + size] for i in range(len(normalized) - size + 1)
        )
    return grams


def _hash_vector(text: str, dim: int = DIM) -> list[float]:
    vector = [0.0] * dim
    for gram in _ngrams(text):
        digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % dim
        vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def _overlap(query: str, content: str) -> float:
    a: dict[str, float] = {}
    for gram in _ngrams(query):
        a[gram] = a.get(gram, 0.0) + 1.0
    b: dict[str, float] = {}
    for gram in _ngrams(content):
        b[gram] = b.get(gram, 0.0) + 1.0
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0.0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


# ---------------------------------------------------------------------------
# schemas
# ---------------------------------------------------------------------------
class EmbeddingRequest(BaseModel):
    model: str = "model-service-embedding"
    input: list[str] = Field(min_length=1)


class RerankRequest(BaseModel):
    model: str = "model-service-reranker"
    query: str
    documents: list[str] = Field(min_length=1)


class ChatMessageIn(BaseModel):
    """One chat message in OpenAI's shape.

    ``content`` is optional because an assistant turn that only asks for tools
    carries ``tool_calls`` instead, and the agent loop replays such turns.
    Multimodal requests use the OpenAI array form:
    ``[{"type": "text", ...}, {"type": "image_url", ...}]`` — passed through
    to the chat template after the qwen backend decodes the images.
    """

    role: str
    content: Optional[Union[str, list[dict[str, Any]]]] = None
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[list[dict[str, Any]]] = None


class ChatRequest(BaseModel):
    model: str = "model-service-chat"
    messages: list[ChatMessageIn]
    stream: bool = False
    temperature: float = 0.2
    max_tokens: Optional[int] = None
    tools: Optional[list[dict[str, Any]]] = None
    tool_choice: Optional[Any] = None


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    body = {"status": "ok", "dim": DIM, "version": VERSION, "llm_backend": LLM_BACKEND}
    if LLM_BACKEND == "qwen":
        try:
            body["model"] = _qwen_module().describe()
        except HTTPException as exc:  # missing module file
            body["model"] = {"backend": "qwen", "error": exc.detail}
    return body


@app.post("/v1/embeddings")
def embeddings(payload: EmbeddingRequest) -> dict:
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": _hash_vector(text)}
            for i, text in enumerate(payload.input)
        ],
        "model": payload.model,
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }


@app.post("/v1/rerank")
def rerank(payload: RerankRequest) -> dict:
    scored = sorted(
        (
            {"index": i, "relevance_score": round(_overlap(payload.query, doc), 6)}
            for i, doc in enumerate(payload.documents)
        ),
        key=lambda item: item["relevance_score"],
        reverse=True,
    )
    return {"model": payload.model, "results": scored}


def _extractive_answer(messages: list[ChatMessageIn]) -> str:
    """Best-matching sentences from the provided context, mock-labelled."""
    system = next((m.content or "" for m in messages if m.role == "system"), "")
    question = next(
        (m.content or "" for m in reversed(messages) if m.role == "user"), ""
    )
    if "【片段" not in system:
        return (
            "（本地 model-service：未检测到知识库上下文。）"
            "这是一个 OpenAI 兼容的占位模型，仅用于本地演示。"
        )
    candidates = [part.strip() for part in system.split("【片段") if part.strip()]
    scored = sorted(
        ((_overlap(question, part), part) for part in candidates),
        key=lambda item: item[0],
        reverse=True,
    )
    top = [part for score, part in scored[:2] if score > 0] or [candidates[0]]
    body = " ".join(part.split("\n", 1)[-1][:200] for part in top)
    return f"（本地 model-service 摘录知识库原文作答）{body}"


@app.post("/v1/chat/completions")
def chat_completions(payload: ChatRequest):
    if LLM_BACKEND == "qwen":
        if payload.stream:
            return _qwen_chat_stream(payload)
        backend = _qwen_or_503()
        try:
            result = backend.complete(
                _messages_for_template(payload.messages),
                tools=payload.tools,
                max_tokens=payload.max_tokens,
                temperature=payload.temperature,
            )
        except Exception as exc:  # noqa: BLE001 - report generation failures
            logger.exception("qwen generation failed")
            raise HTTPException(status_code=502, detail=f"local model failed: {exc}") from exc
        return _chat_completion_payload(payload, result)

    if payload.stream:
        return _chat_stream(payload)

    answer = _extractive_answer(payload.messages)
    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "model": payload.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _chat_stream(payload: ChatRequest):
    """SSE stream in OpenAI's chunk format (stub backend)."""
    answer = _extractive_answer(payload.messages)

    def gen():
        for i in range(0, len(answer), 8):
            chunk = {
                "id": f"chatcmpl-{int(time.time())}",
                "object": "chat.completion.chunk",
                "model": payload.model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": answer[i : i + 8]},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    from fastapi.responses import StreamingResponse

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("MODEL_SERVICE_PORT", "9000")))
