"""Agent execution loop (Phase 5).

Implements the framework's workflow:

    用户问题 → Agent 判断 → [工具调用 → 工具结果 → 再判断]* → 最终答案

Each iteration asks the LLM for a :class:`ToolDecision` (function calling on
real providers, rule-based intent on the mock). Tool calls execute through
the registry, their observations are fed back, and the loop repeats until the
model answers directly or ``MAX_ITERATIONS`` is reached.

The stream emits typed events for the frontend timeline:

    step (thought/tool_call/tool_result) -> delta* -> done
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional, Sequence

from app.agent.tools import BaseTool, ToolContext, ToolError, get_tool, tool_specs
from app.rag.llm import ChatMessage, ToolDecision, get_llm_provider
from app.services.chat_service import ChatStreamEvent

logger = logging.getLogger(__name__)

# Safety bound: a runaway tool loop ends here.
MAX_ITERATIONS = 8
MAX_STEP_SUMMARY = 120

DEFAULT_AGENT_PERSONA = (
    "你是一个能调用工具的中文智能助手。根据用户问题判断需要做什么："
    "需要查资料时调用知识库检索工具，需要分析数据文件时调用数据分析工具；"
    "凡是数值计算，必须调用计算器工具完成，不要自己心算。"
    "工具结果足以回答后，给出简洁、有依据的最终答案（Markdown 格式）。"
    "不要编造工具结果。"
)


@dataclass
class AgentRunResult:
    """Mutable sink the executor fills in; the caller persists it."""

    steps: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    answer: str = ""
    model: str = ""


def _chunk(text: str, size: int = 8) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def _args_summary(arguments: dict[str, Any]) -> str:
    try:
        return json.dumps(arguments, ensure_ascii=False)[:MAX_STEP_SUMMARY]
    except (TypeError, ValueError):
        return str(arguments)[:MAX_STEP_SUMMARY]


async def run_agent_stream(
    ctx: ToolContext,
    *,
    question: str,
    history: Sequence[ChatMessage],
    tool_names: Optional[list[str]] = None,
    system_prompt: str = "",
    env: Optional[dict[str, Any]] = None,
    result: AgentRunResult,
) -> AsyncIterator[ChatStreamEvent]:
    """Run the tool loop, yielding step/delta events; fills ``result``.

    ``env`` carries structured context hints (KB bound? which data files?)
    that offline/mock tool decisions reason over.
    """
    llm = get_llm_provider()
    specs = tool_specs(tool_names)
    system_text = system_prompt or DEFAULT_AGENT_PERSONA
    # Real LLMs can't guess file ids: surface the caller's uploaded data files
    # in the system prompt so data_analysis gets a valid file_id. (The mock's
    # intent matcher reads them from ``env`` instead.)
    uploaded = (env or {}).get("data_files") or []
    if uploaded:
        listing = "\n".join(
            f"- id={item.get('id')}：{item.get('filename')}"
            f"（{len(item.get('columns') or [])} 列）"
            for item in uploaded
        )
        system_text += (
            "\n\n当前用户已上传以下数据文件，调用 data_analysis 工具时必须使用"
            "对应记录里的 file_id（不要猜测其他编号）：\n" + listing
        )
    messages: list[ChatMessage] = [
        {"role": "system", "content": system_text},
        *history,
        {"role": "user", "content": question},
    ]

    def _step(step_type: str, summary: str, tool: Optional[str] = None,
              attachments: Optional[list[dict[str, Any]]] = None) -> ChatStreamEvent:
        return ChatStreamEvent(
            "step",
            {
                "index": len(result.steps) + 1,
                "type": step_type,
                "tool": tool,
                "summary": summary[:MAX_STEP_SUMMARY],
                **({"attachments": attachments} if attachments else {}),
            },
        )

    yield _step("thought", "分析问题，决定行动计划")

    for _iteration in range(MAX_ITERATIONS):
        decision: ToolDecision = await asyncio.to_thread(
            llm.complete_with_tools, messages, specs, env
        )

        if not decision.wants_tools:
            answer = decision.final or "（模型未返回内容）"
            for chunk in _chunk(answer):
                yield ChatStreamEvent("delta", {"text": chunk})
            result.answer = answer
            result.model = llm.signature
            return

        # Record the model's tool requests in the OpenAI message shape so
        # real providers can pair the following role=tool messages.
        messages.append(
            {
                "role": "assistant",
                "content": decision.final or "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(
                                call.arguments, ensure_ascii=False
                            ),
                        },
                    }
                    for call in decision.tool_calls
                ],
            }
        )

        for call in decision.tool_calls:
            yield _step("tool_call", _args_summary(call.arguments), tool=call.name)
            observation = await _execute_tool(call.name, call.arguments, ctx, result)
            yield _step("tool_result", observation.summary, tool=call.name,
                        attachments=observation.attachments or None)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": observation.detail,
                }
            )

    # Exhausted the iteration budget without a final answer.
    fallback = "（已达最大工具调用轮数，请缩小问题范围后重试）"
    for chunk in _chunk(fallback):
        yield ChatStreamEvent("delta", {"text": chunk})
    result.answer = fallback
    result.model = llm.signature


async def _execute_tool(
    name: str, arguments: dict[str, Any], ctx: ToolContext, result: AgentRunResult
) -> Any:
    """Run one tool call and record its outcome; never raises."""

    class _Outcome:
        def __init__(self, summary: str, detail: str,
                     attachments: Optional[list[dict[str, Any]]] = None) -> None:
            self.summary = summary
            self.detail = detail
            self.attachments = attachments or []

    tool: Optional[BaseTool] = get_tool(name)
    if tool is None:
        summary = f"未知工具：{name}"
        return _Outcome(summary, f"工具 {name} 不存在。可用工具：知识检索/计算器/数据分析。")

    try:
        outcome = await tool.run(arguments, ctx)
    except ToolError as exc:
        logger.warning("Tool %s failed: %s", name, exc)
        summary = f"工具执行失败：{exc}"
        detail = f"工具 {name} 执行失败：{exc}。请调整参数或换一种方式回答。"
        return _Outcome(summary, detail)
    except Exception as exc:  # noqa: BLE001 - agent must survive tool crashes
        logger.exception("Tool %s crashed", name)
        summary = f"工具内部错误：{exc}"
        detail = f"工具 {name} 发生内部错误：{exc}。"
        return _Outcome(summary, detail)

    result.steps.append(
        {
            "type": "tool_result",
            "tool": name,
            "summary": outcome.summary[:MAX_STEP_SUMMARY],
        }
    )
    result.sources.extend(outcome.sources)
    result.attachments.extend(outcome.attachments)
    return _Outcome(outcome.summary, outcome.detail, outcome.attachments)


def build_agent_env(kb_id: Optional[int], data_file_rows: Sequence[Any]) -> dict[str, Any]:
    """Structured environment hints for offline/mock tool decisions."""
    return {
        "has_kb": kb_id is not None,
        "data_files": [
            {
                "id": row.id,
                "filename": row.filename,
                "columns": row.column_names or [],
            }
            for row in data_file_rows
        ],
    }
