"""The self-hosted MCP server: five tools per the framework document.

    calculator | knowledge_search | weather | http_request | python_analysis

The server is protocol-only: every tool body delegates to the same
implementations the rest of the platform uses (calculator -> safe AST eval,
knowledge_search -> the Phase 3 retrieval pipeline, python_analysis -> the
Phase 5 sandbox namespace), so behaviour never drifts between surfaces.

Security notes baked in:

* ``knowledge_search`` requires an explicit ``kb_id`` — the MCP surface has
  no user session, so the caller scopes the search. (A production deployment
  would forward the user's auth context into the session instead.)
* ``http_request`` blocks non-HTTP schemes and private/loopback targets
  (SSRF guard).
* ``python_analysis`` runs through the same import whitelist / restricted
  builtins as the agent sandbox.
"""

from __future__ import annotations

import ast
import concurrent.futures
import io
import ipaddress
import logging
import socket
from contextlib import redirect_stdout
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from app.agent.sandbox import SAFE_BUILTINS, _save_open_figures
from app.agent.tools import _check_analysis_imports, _safe_eval
from app.rag.retrieval import RetrievalPipeline

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# tool descriptors (MCP inputSchema form)
# ---------------------------------------------------------------------------
TOOLS: list[dict[str, Any]] = [
    {
        "name": "calculator",
        "description": "精确计算四则运算表达式（+ - * / // % **、括号、abs/round/min/max/sqrt/log）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "算术表达式"}
            },
            "required": ["expression"],
        },
    },
    {
        "name": "knowledge_search",
        "description": (
            "在指定知识库（kb_id）中检索最相关的文档片段，返回内容与来源"
            "（文件名、页码）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "kb_id": {"type": "integer", "description": "知识库 id"},
                "query": {"type": "string", "description": "检索关键词或问题"},
            },
            "required": ["kb_id", "query"],
        },
    },
    {
        "name": "weather",
        "description": "查询城市当前天气（Open-Meteo 实时数据；网络不可用时返回标注的示例数据）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名（中英文均可）"}
            },
            "required": ["city"],
        },
    },
    {
        "name": "http_request",
        "description": "对公网 URL 发起 GET 请求并返回状态码与响应体摘要（禁止内网地址）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "完整 http/https URL"}
            },
            "required": ["url"],
        },
    },
    {
        "name": "python_analysis",
        "description": (
            "在受限沙箱中执行一段 pandas 代码（变量 df 已由可选 csv_text 加载），"
            "返回 print 输出；仅允许 pandas/numpy/matplotlib/math。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python 分析代码"},
                "csv_text": {"type": "string", "description": "可选：作为 df 的 CSV 数据"},
            },
            "required": ["code"],
        },
    },
]


def list_tools() -> list[dict[str, Any]]:
    return TOOLS


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------
async def call_tool(name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
    """Execute one MCP tool; returns (text, is_error)."""
    if name == "calculator":
        return _calculator(arguments)
    if name == "knowledge_search":
        return await _knowledge_search(arguments)
    if name == "weather":
        return await _weather(arguments)
    if name == "http_request":
        return await _http_request(arguments)
    if name == "python_analysis":
        return _python_analysis(arguments)
    return f"未知工具：{name}。可用：{', '.join(t['name'] for t in TOOLS)}", True


# ---------------------------------------------------------------------------
# implementations
# ---------------------------------------------------------------------------
def _calculator(args: dict[str, Any]) -> tuple[str, bool]:
    expression = str(args.get("expression") or "").strip()
    if not expression:
        return "参数错误：expression 不能为空", True
    try:
        tree = ast.parse(expression, mode="eval")
        value = _safe_eval(tree)
    except SyntaxError as exc:
        return f"表达式语法错误: {expression}", True
    except Exception as exc:  # noqa: BLE001 - ToolError from the safe evaluator
        return f"不支持的表达式：{exc}", True
    formatted = f"{value:g}" if isinstance(value, float) else str(value)
    return f"计算结果：{expression} = {formatted}", False


async def _knowledge_search(args: dict[str, Any]) -> tuple[str, bool]:
    kb_id = args.get("kb_id")
    query = str(args.get("query") or "").strip()
    if not isinstance(kb_id, int) or not query:
        return "参数错误：需要整数 kb_id 和非空 query", True

    from app.core.database import SessionLocal

    with SessionLocal() as db:
        pipeline = RetrievalPipeline(db)
        chunks = pipeline.retrieve(kb_id, query)
        if not chunks:
            return "知识库中没有找到相关内容。", False
        lines = [
            f"【片段 {i}】{chunk.source}\n{chunk.snippet}"
            for i, chunk in enumerate(chunks, 1)
        ]
        return "\n\n".join(lines), False


_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


async def _weather(args: dict[str, Any]) -> tuple[str, bool]:
    city = str(args.get("city") or "").strip()
    if not city:
        return "参数错误：city 不能为空", True

    try:
        async with httpx.AsyncClient(timeout=6) as client:
            geo = (
                await client.get(_GEOCODE_URL, params={"name": city, "language": "zh", "count": 1})
            ).json()
            results = geo.get("results") or []
            if not results:
                return f"未找到城市「{city}」。", False
            spot = results[0]
            forecast = (
                await client.get(
                    _FORECAST_URL,
                    params={
                        "latitude": spot["latitude"],
                        "longitude": spot["longitude"],
                        "current_weather": True,
                    },
                )
            ).json()
        current = forecast.get("current_weather") or {}
        temp = current.get("temperature")
        wind = current.get("windspeed")
        return (
            f"{spot.get('name', city)} 当前气温 {temp}°C，风速 {wind} km/h"
            f"（数据来源：Open-Meteo）"
        ), False
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.warning("Weather API unavailable (%s); returning sample data", exc)
        return (
            f"{city} 当前气温 23.0°C，风速 12 km/h"
            "（离线示例数据：天气服务不可达）"
        ), False


# Hosts that must never be fetched through http_request (SSRF guard).
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "metadata.google.internal"}


def _is_public_http_url(raw_url: str) -> tuple[bool, str]:
    try:
        parsed = urlparse(raw_url)
    except ValueError:
        return False, "URL 解析失败"
    if parsed.scheme not in ("http", "https"):
        return False, f"仅允许 http/https，收到：{parsed.scheme or '(空)'}"
    host = parsed.hostname or ""
    if not host or host.lower() in _BLOCKED_HOSTS:
        return False, f"禁止访问内网/保留主机：{host}"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False, f"无法解析主机：{host}"
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if not addr.is_global:
            return False, f"主机解析到非公网地址 {addr}，已阻止"
    return True, ""


async def _http_request(args: dict[str, Any]) -> tuple[str, bool]:
    url = str(args.get("url") or "").strip()
    ok, reason = _is_public_http_url(url)
    if not ok:
        return f"已拒绝请求：{reason}", True
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        return f"请求失败：{exc}", True
    content_type = response.headers.get("content-type", "")
    body = response.text[:1000]
    return f"HTTP {response.status_code} · {content_type}\n\n{body}", False


_ANALYSIS_TIMEOUT_SECONDS = 30


def _python_analysis(args: dict[str, Any]) -> tuple[str, bool]:
    code = str(args.get("code") or "")
    csv_text = args.get("csv_text")
    if not code.strip():
        return "参数错误：code 不能为空", True
    try:
        _check_analysis_imports(code)
    except Exception as exc:  # noqa: BLE001 - ToolError-style guard message
        return f"代码被沙箱拒绝：{exc}", True

    import math

    import numpy as np
    import pandas as pd
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    df = pd.read_csv(io.StringIO(str(csv_text))) if csv_text else pd.DataFrame()
    namespace: dict[str, Any] = {
        "__builtins__": SAFE_BUILTINS,
        "pd": pd,
        "np": np,
        "plt": plt,
        "math": math,
        "df": df,
    }

    stdout_capture = io.StringIO()
    error: Optional[str] = None
    try:
        with redirect_stdout(stdout_capture):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(exec, code, namespace)  # noqa: S102 - sandboxed
                future.result(timeout=_ANALYSIS_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        error = f"分析超时（>{_ANALYSIS_TIMEOUT_SECONDS}s）"
    except Exception as exc:  # noqa: BLE001 - reported to the caller
        error = f"{type(exc).__name__}: {exc}"

    parts = [stdout_capture.getvalue().strip()[:3000]]
    if error:
        parts.append(f"脚本出错：{error}")
    chart_url = _save_open_figures(plt)
    if chart_url:
        parts.append(f"图表已生成：{chart_url}")
    text = "\n".join(part for part in parts if part) or "（脚本没有输出）"
    return text, error is not None
