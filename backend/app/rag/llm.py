"""LLM provider abstraction (Phase 4: sync + streaming).

Phase 4 adds the streaming half: ``stream_complete`` / ``stream_rag_answer``
are async generators consumed by the SSE chat endpoint. The sync ``complete``
/ ``rag_answer`` remain for the Phase 3 RAG endpoint and internal callers.

* :class:`MockLLMProvider` — offline extractive answer over the retrieved
  context (no network, deterministic). Default so demos and tests run
  without API keys; the response is clearly labelled as mock output.
* :class:`OpenAICompatibleChatProvider` — any ``/v1/chat/completions``
  endpoint (OpenAI, DeepSeek, Zhipu, SiliconFlow, ...), SSE streaming
  supported.
"""

from __future__ import annotations

import asyncio
import ast
import json
import logging
import math
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, AsyncIterator, Sequence

import httpx

from app.core.config import settings
from app.rag.embeddings import _char_ngrams

logger = logging.getLogger(__name__)

# Streaming chunk size for providers that materialise the full text first
# (mock mode). CJK-friendly: cut on characters, not bytes.
_STREAM_CHUNK_CHARS = 8
_STREAM_CHUNK_DELAY = 0.02

# --- mock intent detection -------------------------------------------------
_ANALYSIS_KEYWORDS = ("分析", "趋势", "异常", "统计", "汇总", "图表", "环比", "同比")


class LLMError(RuntimeError):
    """Raised when an LLM provider fails or returns unusable output."""


@dataclass(frozen=True)
class ContextBlock:
    """One retrieved chunk rendered into the prompt.

    ``source`` is a human-readable locator (filename · page) that the model is
    instructed to cite, keeping answers traceable to documents.
    """

    index: int  # 1-based citation number shown to the model
    source: str  # e.g. "产品手册.pdf · 第 3 页"
    content: str


@dataclass(frozen=True)
class ToolCallRequest:
    """One function call the model wants executed (Phase 5 agent loop)."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolDecision:
    """Result of one agent iteration: either a final answer or tool calls."""

    final: Optional[str]
    tool_calls: list[ToolCallRequest]

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


# Environment hints for offline/mock tool decisions: what exists in the
# caller's context (KB bound? which data files?). Real LLMs see tools in the
# prompt; the mock needs this structured context to imitate intent detection.
MockEnv = dict[str, Any]


ChatMessage = dict[str, str]  # {"role": "system"|"user"|"assistant", "content": str}


class LLMProvider(ABC):
    """Common interface for text generation (sync + streaming)."""

    name: str = "base"

    @abstractmethod
    def complete(self, messages: Sequence[ChatMessage]) -> str:
        """Run one non-streaming completion over a message list."""

    @property
    def signature(self) -> str:
        return self.name

    def rag_answer(self, question: str, blocks: Sequence[ContextBlock]) -> str:
        """Answer a question grounded in the given retrieved blocks.

        The default renders the standard RAG prompt and calls
        :meth:`complete`; offline providers may override it.
        """
        return self.complete(build_rag_messages(question, blocks))

    # ---------- streaming (Phase 4) ----------
    async def stream_complete(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        """Stream a completion as text deltas.

        The default materialises the sync answer in a worker thread and emits
        it in small chunks — correct for offline/mock mode. Real providers
        override this with genuine SSE streaming.
        """
        text = await asyncio.to_thread(self.complete, messages)
        for chunk in _chunk_text(text):
            yield chunk
            await asyncio.sleep(_STREAM_CHUNK_DELAY)

    async def stream_rag_answer(
        self, question: str, blocks: Sequence[ContextBlock]
    ) -> AsyncIterator[str]:
        """Stream a grounded answer; default wraps :meth:`rag_answer`."""
        text = await asyncio.to_thread(self.rag_answer, question, blocks)
        for chunk in _chunk_text(text):
            yield chunk
            await asyncio.sleep(_STREAM_CHUNK_DELAY)

    # ---------- function calling (Phase 5 agent loop) ----------
    def complete_with_tools(
        self,
        messages: Sequence[ChatMessage],
        tool_specs: Sequence[Any],
        env: Optional[MockEnv] = None,
    ) -> ToolDecision:
        """One agent iteration: answer directly or request tool calls.

        ``tool_specs`` are :class:`~app.agent.tools.ToolSpec` objects. The
        base implementation has no tool support and always answers directly.
        """
        return ToolDecision(final=self.complete(messages), tool_calls=[])


def _chunk_text(text: str, size: int = _STREAM_CHUNK_CHARS) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def build_rag_messages(
    question: str, blocks: Sequence[ContextBlock]
) -> list[ChatMessage]:
    """Render the shared RAG prompt.

    The system prompt forces grounded answers with ``【来源 i】`` citations so
    the frontend can highlight which document/page each claim came from. The
    "answer body first" instruction leads deliberately: small local models
    tend to short-circuit to whichever instruction comes last, producing a
    bare citation line with no answer.
    """
    if not blocks:
        context = "（知识库中没有检索到相关内容）"
    else:
        rendered = "\n\n".join(
            f"【片段 {block.index}】来源：{block.source}\n{block.content}"
            for block in blocks
        )
        context = rendered

    system = (
        "你是一个严谨的中文知识库问答助手。"
        "收到问题后，先直接用完整的中文句子回答问题本体（这是回答的主体，"
        "至少一两句话，绝不能省略），回答只依据下面提供的检索片段，"
        "不要编造知识库以外的内容；如果片段不足以回答，请明确说明。"
        "最后另起一行，用『来源：』列出引用的片段编号，例如：来源：【片段 1】【片段 3】。"
        f"\n\n=== 检索片段 ===\n{context}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"问题：{question}"},
    ]


class MockLLMProvider(LLMProvider):
    """Offline extractive "LLM".

    Scores sentences in the retrieved context by character n-gram overlap with
    the question and stitches the best ones into an answer. It is deliberately
    NOT a language model — it exists so the whole RAG path (embedding →
    pgvector → rerank → prompt → answer → citations) is demoable and testable
    without any API key. Output is labelled accordingly.
    """

    name = "mock"

    def complete(self, messages: Sequence[ChatMessage]) -> str:
        return (
            "（当前为离线 mock LLM，未接入真实模型。）"
            "请在 .env 中配置 LLM_PROVIDER=openai 与 LLM_API_KEY 以获得生成式回答。"
        )

    def rag_answer(self, question: str, blocks: Sequence[ContextBlock]) -> str:
        picked = self._extract(question, blocks)
        if not picked:
            return (
                "（离线 mock 模式）知识库中没有找到与问题相关的内容，"
                "无法给出有依据的回答。"
            )
        body = " ".join(sentence for sentence, _ in picked)
        citations = "、".join(
            f"【片段 {block.index}】" for _, block in picked
        )
        return (
            f"（离线 mock 模式，摘录知识库原文作答）{body}\n来源：{citations}"
        )

    def _extract(
        self, question: str, blocks: Sequence[ContextBlock]
    ) -> list[tuple[str, ContextBlock]]:
        """Pick up to three sentences that best match the question."""
        query_counts = _weighted_grams(question)
        scored: list[tuple[float, int, str, ContextBlock]] = []
        for block in blocks:
            for position, sentence in enumerate(_sentences(block.content)):
                counts = _weighted_grams(sentence)
                if not counts:
                    continue
                score = _cosine(query_counts, counts)
                if score > 0:
                    scored.append((score, -position, sentence, block))

        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        picked: list[tuple[str, ContextBlock]] = []
        seen: set[str] = set()
        for _, _, sentence, block in scored:
            key = sentence[:64]
            if key in seen:
                continue
            seen.add(key)
            picked.append((sentence, block))
            if len(picked) >= 3:
                break
        return picked

    # ---------- function calling: rule-based intent ----------
    def complete_with_tools(
        self,
        messages: Sequence[ChatMessage],
        tool_specs: Sequence[Any],
        env: Optional[MockEnv] = None,
    ) -> ToolDecision:
        """Deterministic stand-in for LLM function calling.

        The mock cannot *understand* tool schemas, so it pattern-matches the
        question against the available environment:

        1. a finished tool observation in the thread -> synthesise the answer
        2. data-analysis keywords + an uploaded file    -> ``data_analysis``
        3. an arithmetic expression in the question     -> ``calculator``
        4. a bound knowledge base                       -> ``knowledge_search``
        5. otherwise                                    -> direct mock answer
        """
        spec_names = {spec.name for spec in tool_specs}
        env = env or {}

        tool_messages = [m for m in messages if m.get("role") == "tool"]
        if tool_messages:
            return ToolDecision(
                final=self._synthesise(tool_messages[-1]["content"]), tool_calls=[]
            )

        question = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"),
            "",
        )

        def _call(name: str, arguments: dict[str, Any]) -> ToolDecision:
            return ToolDecision(
                final=None,
                tool_calls=[
                    ToolCallRequest(
                        id=f"mock_{uuid.uuid4().hex[:8]}",
                        name=name,
                        arguments=arguments,
                    )
                ],
            )

        data_files = env.get("data_files") or []
        if (
            "data_analysis" in spec_names
            and data_files
            and any(keyword in question for keyword in _ANALYSIS_KEYWORDS)
        ):
            file_id = _match_data_file(question, data_files)
            return _call(
                "data_analysis",
                {"file_id": file_id, "question": question},
            )

        # Prefer the MCP-hosted calculator when discovery registered one, so
        # the demo timeline visibly exercises the MCP path.
        calc_name: str | None = None
        if "mcp_calculator" in spec_names:
            calc_name = "mcp_calculator"
        elif "calculator" in spec_names:
            calc_name = "calculator"
        if calc_name:
            expression = _extract_arithmetic(question)
            if expression:
                return _call(calc_name, {"expression": expression})

        if "knowledge_search" in spec_names and env.get("has_kb"):
            return _call("knowledge_search", {"query": question})

        return ToolDecision(
            final=(
                "（当前为离线 mock LLM，未接入真实模型。）"
                "请在 .env 中配置 LLM_PROVIDER=openai 与 LLM_API_KEY 以获得生成式回答。"
            ),
            tool_calls=[],
        )

    def _synthesise(self, tool_content: str) -> str:
        """Turn the last tool observation into a labelled final answer."""
        if tool_content.startswith("计算结果："):
            return f"计算完成。{tool_content}\n（离线 mock 模式）"
        if "【片段" in tool_content:
            return (
                "根据知识库检索结果：\n\n"
                + tool_content
                + "\n\n（离线 mock 模式，摘录检索原文作答）"
            )
        return (
            "数据分析完成：\n\n"
            + tool_content[:1500]
            + "\n\n（离线 mock 模式，输出为脚本的统计结果）"
        )


def _sentences(text: str) -> list[str]:
    """Split on CJK/latin sentence enders, keeping pieces reasonably short."""
    raw = text.replace("\n", " ").split("。")
    sentences: list[str] = []
    for chunk in raw:
        chunk = chunk.strip("；;，, 　")
        if not chunk:
            continue
        if len(chunk) > 160:
            chunk = chunk[:160]
        sentences.append(chunk)
    return sentences


def _extract_arithmetic(question: str) -> str | None:
    """Find the longest balanced arithmetic expression in the question.

    A small scanner instead of a regex: regexes can't match balanced
    parentheses, so ``(1200+980)*1.12`` inside prose would otherwise come out
    mangled (``1200+980)*1.12``) and fail to parse.
    """
    best: str | None = None
    length = len(question)
    for start in range(length):
        head = question[start]
        if not (head.isdigit() or head == "("):
            continue
        depth = 0
        for end in range(start, length):
            ch = question[end]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth < 0:
                    break
            elif not (ch.isdigit() or ch in "+-*/%. " or ch in "×÷"):
                break
            if depth != 0:
                continue
            candidate = question[start : end + 1].strip()
            if not any(op in candidate for op in "+-*/%×÷"):
                continue
            if not (candidate[-1].isdigit() or candidate[-1] == ")"):
                continue
            try:
                ast.parse(
                    candidate.replace("×", "*").replace("÷", "/"), mode="eval"
                )
            except SyntaxError:
                continue
            if best is None or len(candidate) > len(best):
                best = candidate
    return best


def _match_data_file(question: str, data_files: list[dict[str, Any]]) -> int:
    """Pick the data file the question refers to (name match, else the first)."""
    for entry in data_files:
        stem = str(entry.get("filename") or "").rsplit(".", 1)[0]
        if stem and stem in question:
            return int(entry["id"])
    return int(data_files[0]["id"])


def _weighted_grams(text: str) -> dict[str, float]:
    counts: dict[str, float] = {}
    for gram in _char_ngrams(text):
        counts[gram] = counts.get(gram, 0.0) + 1.0
    return {gram: 1.0 + math.log(count) for gram, count in counts.items()}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(value * b.get(key, 0.0) for key, value in a.items())
    norm_a = math.sqrt(sum(value * value for value in a.values()))
    norm_b = math.sqrt(sum(value * value for value in b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class OpenAICompatibleChatProvider(LLMProvider):
    """Client for any OpenAI-compatible ``POST {base}/chat/completions``."""

    name = "openai"

    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = (
            settings.LLM_TEMPERATURE if temperature is None else temperature
        )
        self.max_tokens = (
            settings.LLM_MAX_OUTPUT_TOKENS if max_tokens is None else max_tokens
        )
        self.timeout = timeout or settings.LLM_TIMEOUT_SECONDS

    @property
    def signature(self) -> str:
        return f"openai:{self.model}"

    def complete(self, messages: Sequence[ChatMessage]) -> str:
        if not self.api_base or not self.api_key or not self.model:
            raise LLMError(
                "LLM_PROVIDER=openai requires LLM_API_BASE, LLM_API_KEY and "
                "LLM_MODEL"
            )
        try:
            response = httpx.post(
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": list(messages),
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                    "stream": False,
                },
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM API request failed: {exc}") from exc

        if response.status_code >= 400:
            raise LLMError(
                f"LLM API returned {response.status_code}: {response.text[:300]}"
            )

        payload: dict[str, Any] = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"LLM API returned an unexpected payload: {exc}") from exc
        if not isinstance(content, str):
            raise LLMError("LLM API returned a non-text message")
        return content

    # ---------- streaming ----------
    async def stream_complete(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        """Stream deltas from a ``stream=true`` chat-completions call."""
        if not self.api_base or not self.api_key or not self.model:
            raise LLMError(
                "LLM_PROVIDER=openai requires LLM_API_BASE, LLM_API_KEY and "
                "LLM_MODEL"
            )
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.api_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": list(messages),
                        "temperature": self.temperature,
                        "max_tokens": self.max_tokens,
                        "stream": True,
                    },
                ) as response:
                    if response.status_code >= 400:
                        body = (await response.aread()).decode("utf-8", "replace")
                        raise LLMError(
                            f"LLM API returned {response.status_code}: {body[:300]}"
                        )
                    async for line in response.aiter_lines():
                        delta = _parse_stream_line(line)
                        if delta:
                            yield delta
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM API request failed: {exc}") from exc

    async def stream_rag_answer(
        self, question: str, blocks: Sequence[ContextBlock]
    ) -> AsyncIterator[str]:
        async for chunk in self.stream_complete(build_rag_messages(question, blocks)):
            yield chunk

    # ---------- function calling ----------
    def complete_with_tools(
        self,
        messages: Sequence[ChatMessage],
        tool_specs: Sequence[Any],
        env: Optional[MockEnv] = None,
    ) -> ToolDecision:
        """One agent iteration via the OpenAI ``tools`` API (non-streaming)."""
        if not self.api_base or not self.api_key or not self.model:
            raise LLMError(
                "LLM_PROVIDER=openai requires LLM_API_BASE, LLM_API_KEY and "
                "LLM_MODEL"
            )
        tools = [spec.as_openai_tool() for spec in tool_specs]
        body: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        try:
            response = httpx.post(
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM API request failed: {exc}") from exc

        if response.status_code >= 400:
            raise LLMError(
                f"LLM API returned {response.status_code}: {response.text[:300]}"
            )

        payload: dict[str, Any] = response.json()
        try:
            message = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"LLM API returned an unexpected payload: {exc}") from exc

        raw_calls = message.get("tool_calls") or []
        if raw_calls:
            requests: list[ToolCallRequest] = []
            for call in raw_calls:
                try:
                    arguments = json.loads(call["function"].get("arguments") or "{}")
                except (json.JSONDecodeError, TypeError, KeyError):
                    arguments = {}
                requests.append(
                    ToolCallRequest(
                        id=str(call.get("id") or f"call_{uuid.uuid4().hex[:8]}"),
                        name=str(call["function"].get("name") or ""),
                        arguments=arguments if isinstance(arguments, dict) else {},
                    )
                )
            return ToolDecision(final=message.get("content"), tool_calls=requests)
        return ToolDecision(final=message.get("content") or "", tool_calls=[])


def _parse_stream_line(line: str) -> str | None:
    """Extract one content delta from an SSE ``data:`` line.

    Returns ``None`` for comments, keep-alives, ``[DONE]`` and deltas that
    carry no text (role-only or tool-call chunks).
    """
    line = line.strip()
    if not line.startswith("data:"):
        return None
    data = line[len("data:"):].strip()
    if not data or data == "[DONE]":
        return None
    try:
        payload = json.loads(data)
        delta = payload["choices"][0].get("delta") or {}
        content = delta.get("content")
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return None
    return content if isinstance(content, str) and content else None


@lru_cache(maxsize=1)
def get_llm_provider() -> LLMProvider:
    """Build the provider selected by ``LLM_PROVIDER`` (cached)."""
    provider = settings.LLM_PROVIDER
    if provider == "mock":
        return MockLLMProvider()
    if provider == "openai":
        return OpenAICompatibleChatProvider(
            api_base=settings.LLM_API_BASE,
            api_key=settings.LLM_API_KEY,
            model=settings.LLM_MODEL,
        )
    raise LLMError(f"Unknown LLM_PROVIDER '{provider}'")
