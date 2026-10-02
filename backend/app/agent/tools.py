"""Agent tool registry (Phase 5).

Tools are the Agent's hands: each tool exposes a name/description/JSON-schema
(often fed to the LLM as function-calling definitions) and an async ``run``
that receives parsed arguments plus a :class:`ToolContext`.

Shipped tools map to the framework's Agent workflow branches:

* ``knowledge_search``  — RAG retrieval over the conversation's KB
* ``calculator``        — safe arithmetic (ast, no eval)
* ``data_analysis``     — Pandas/Matplotlib analysis of an uploaded file

MCP tools (Phase 6) plug into the same interface.
"""

from __future__ import annotations

import ast
import json
import math
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.agent import DataFile
from app.rag.retrieval import RetrievalPipeline


class ToolError(RuntimeError):
    """Tool execution failure surfaced to the agent as an observation."""


@dataclass
class ToolContext:
    """Everything a tool may touch, resolved once per agent run."""

    db: Session
    user_id: int
    knowledge_base_id: Optional[int]  # conversation's KB binding (may be None)
    data_files: dict[int, DataFile] = field(default_factory=dict)


@dataclass
class ToolResult:
    """Outcome of one tool execution."""

    # One-line summary shown in the frontend step timeline.
    summary: str
    # Full observation fed back to the LLM for the next decision.
    detail: str
    # Optional attachments (e.g. generated chart URLs) shown under the step.
    attachments: list[dict[str, Any]] = field(default_factory=list)
    # RAG citations, so the final answer can carry sources like Phase 3.
    sources: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ToolSpec:
    """Everything the LLM needs to decide to call the tool."""

    name: str
    description: str
    parameters: dict  # JSON schema, OpenAI function-calling shape

    def as_openai_tool(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.spec_name(),
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def spec_name(self) -> str:
        return self.name


class BaseTool(ABC):
    """A callable unit the agent can invoke."""

    spec: ToolSpec

    @abstractmethod
    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Execute the tool with validated arguments."""


# ---------------------------------------------------------------------------
# knowledge_search
# ---------------------------------------------------------------------------
class KnowledgeSearchTool(BaseTool):
    spec = ToolSpec(
        name="knowledge_search",
        description=(
            "在知识库中检索与查询最相关的文档片段，返回内容摘要与来源"
            "（文件名、页码）。回答事实性问题或引用资料前先调用本工具。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索关键词或完整问题",
                }
            },
            "required": ["query"],
        },
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            raise ToolError("query 不能为空")
        if ctx.knowledge_base_id is None:
            return ToolResult(
                summary="未绑定知识库，跳过检索",
                detail="当前会话没有绑定知识库，无法检索。请直接基于已知信息回答。",
            )

        pipeline = RetrievalPipeline(ctx.db)
        chunks = pipeline.retrieve(ctx.knowledge_base_id, query)
        if not chunks:
            return ToolResult(
                summary=f"检索「{query}」无结果",
                detail="知识库中没有找到相关内容。请如实告知用户无法从知识库找到依据。",
            )

        lines = [
            f"【片段 {i}】{chunk.source}\n{chunk.snippet}"
            for i, chunk in enumerate(chunks, 1)
        ]
        from app.services.rag_service import _to_source  # reuse the serialiser

        sources = [
            _to_source(chunk, index=i).model_dump()
            for i, chunk in enumerate(chunks, 1)
        ]
        return ToolResult(
            summary=f"检索到 {len(chunks)} 条相关内容",
            detail="\n\n".join(lines),
            sources=sources,
        )


# ---------------------------------------------------------------------------
# calculator
# ---------------------------------------------------------------------------
_ALLOWED_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
}
_ALLOWED_UNARY = {ast.UAdd: lambda a: +a, ast.USub: lambda a: -a}
_ALLOWED_FUNCS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "log": math.log,
}


def _safe_eval(node: ast.AST) -> float:
    """Evaluate a pure-arithmetic AST — no names, no attribute access."""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        return _ALLOWED_BINOPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
        return _ALLOWED_UNARY[type(node.op)](_safe_eval(node.operand))
    if isinstance(node, ast.Call):
        func_name = node.func.id if isinstance(node.func, ast.Name) else None
        if func_name in _ALLOWED_FUNCS and not node.keywords:
            return _ALLOWED_FUNCS[func_name](*[_safe_eval(a) for a in node.args])
    raise ToolError(f"不支持的表达式元素: {ast.dump(node)[:80]}")


class CalculatorTool(BaseTool):
    spec = ToolSpec(
        name="calculator",
        description="精确计算四则运算表达式（支持 + - * / // % **、括号和 abs/round/min/max/sqrt/log）。",
        parameters={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "算术表达式，例如 (1200+980)*1.12"}
            },
            "required": ["expression"],
        },
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        expression = str(args.get("expression") or "").strip()
        if not expression:
            raise ToolError("expression 不能为空")
        try:
            tree = ast.parse(expression, mode="eval")
            value = _safe_eval(tree)
        except ToolError:
            raise
        except SyntaxError as exc:
            raise ToolError(f"表达式语法错误: {expression}") from exc
        formatted = f"{value:g}" if isinstance(value, float) else str(value)
        return ToolResult(
            summary=f"{expression} = {formatted}",
            detail=f"计算结果：{expression} = {formatted}",
        )


# ---------------------------------------------------------------------------
# data_analysis
# ---------------------------------------------------------------------------
# Imports the sandboxed analysis code may use. Everything else is rejected
# before exec, so the generated code cannot touch the filesystem, network or
# interpreter internals.
ANALYSIS_ALLOWED_IMPORTS = {"pandas", "numpy", "matplotlib", "math"}


def _check_analysis_imports(code: str) -> None:
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in ANALYSIS_ALLOWED_IMPORTS:
                    raise ToolError(f"分析代码不允许导入: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in ANALYSIS_ALLOWED_IMPORTS:
                raise ToolError(f"分析代码不允许导入: {node.module}")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ToolError("分析代码不允许访问双下划线属性")


class DataAnalysisTool(BaseTool):
    """Analyse an uploaded Excel/CSV with Pandas and draw a Matplotlib chart.

    The LLM writes a small pandas script operating on a preloaded ``df``;
    the script is AST-checked, executed in a restricted namespace and any
    open Matplotlib figure is persisted as a PNG served to the frontend.
    In mock mode a deterministic template script is used so the full loop is
    demoable offline.
    """

    spec = ToolSpec(
        name="data_analysis",
        description=(
            "用 Pandas 分析已上传的数据文件（按 file_id 指定），完成统计汇总、"
            "趋势/异常分析并生成图表。回答数据分析类问题时调用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "file_id": {"type": "integer", "description": "要分析的数据文件 id"},
                "question": {"type": "string", "description": "分析目标，例如“各月销售趋势与异常月份”"},
            },
            "required": ["file_id", "question"],
        },
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from app.agent.sandbox import run_analysis  # imported lazily (heavy deps)

        try:
            file_id = int(args.get("file_id"))
        except (TypeError, ValueError) as exc:
            raise ToolError("file_id 必须是整数") from exc

        data_file = ctx.data_files.get(file_id)
        if data_file is None:
            data_file = ctx.db.get(DataFile, file_id)
        if data_file is None or data_file.user_id != ctx.user_id:
            raise ToolError(f"数据文件 {file_id} 不存在")

        question = str(args.get("question") or "").strip() or "分析该数据文件"
        outcome = run_analysis(data_file, question, ctx.db)

        detail = (
            f"数据文件：{data_file.filename}（{data_file.row_count} 行）\n"
            f"分析目标：{question}\n\n{outcome.summary}"
        )
        if outcome.chart_url:
            detail += f"\n\n已生成图表：{outcome.chart_url}"
        return ToolResult(
            summary=f"完成数据分析：{outcome.headline}",
            detail=detail,
            attachments=(
                [{"type": "chart", "url": outcome.chart_url}] if outcome.chart_url else []
            ),
        )


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------
_TOOL_TYPES: list[type[BaseTool]] = [
    KnowledgeSearchTool,
    CalculatorTool,
    DataAnalysisTool,
]

_TOOL_CACHE: dict[str, BaseTool] = {}
# Dynamically registered tools (Phase 6: MCP proxies). Kept separate so a
# re-discovery can replace them without touching the builtin cache.
_EXTRA_TOOLS: dict[str, BaseTool] = {}


def register_extra_tool(tool: BaseTool) -> None:
    """Add a dynamically discovered tool (e.g. an MCP proxy)."""
    _EXTRA_TOOLS[tool.spec.name] = tool


def clear_extra_tools() -> None:
    """Drop all dynamically registered tools (before a re-discovery)."""
    _EXTRA_TOOLS.clear()


def get_tool_registry() -> dict[str, BaseTool]:
    """Name -> tool instance: builtins + dynamic extras (constructed lazily)."""
    if not _TOOL_CACHE:
        for tool_type in _TOOL_TYPES:
            tool = tool_type()
            _TOOL_CACHE[tool.spec.name] = tool
    merged = dict(_TOOL_CACHE)
    merged.update(_EXTRA_TOOLS)
    return merged


def get_tool(name: str) -> Optional[BaseTool]:
    return get_tool_registry().get(name)


def tool_specs(names: Optional[list[str]] = None) -> list[ToolSpec]:
    registry = get_tool_registry()
    if names is None:
        return [tool.spec for tool in registry.values()]
    return [registry[name].spec for name in names if name in registry]


def sync_tool_rows(db: Session) -> None:
    """Mirror the code registry into the ``tools`` table (idempotent)."""
    from app.models.agent import Tool

    for tool in get_tool_registry().values():
        row = db.query(Tool).filter(Tool.name == tool.spec.name).one_or_none()
        if row is None:
            db.add(
                Tool(
                    name=tool.spec.name,
                    description=tool.spec.description,
                    type="builtin",
                    enabled=True,
                )
            )
        else:
            row.description = tool.spec.description
    db.commit()


def export_registry_json() -> str:
    return json.dumps(
        [asdict(tool.spec) for tool in get_tool_registry().values()],
        ensure_ascii=False,
        indent=2,
    )
