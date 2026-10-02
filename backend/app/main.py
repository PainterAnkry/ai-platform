"""FastAPI application entrypoint."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.redis import redis_client

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    configure_logging()
    logger.info(
        "Starting %s v%s in %s mode",
        settings.PROJECT_NAME,
        __version__,
        settings.APP_ENV,
    )
    await redis_client.connect()
    # Mirror the code-defined tool registry into the tools table (Phase 5).
    try:
        from app.agent.tools import sync_tool_rows
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            sync_tool_rows(db)
    except Exception as exc:  # noqa: BLE001 - startup must not die on this
        logger.warning("Tool registry sync skipped: %s", exc)
    # Phase 6: discover MCP tools once the server is listening (the client
    # connects to our own /api/mcp over loopback HTTP).
    if settings.MCP_ENABLED:
        asyncio.get_running_loop().create_task(_discover_mcp_later())
    try:
        yield
    finally:
        await redis_client.disconnect()
        logger.info("Shutdown complete")


async def _discover_mcp_later() -> None:
    await asyncio.sleep(1.0)
    try:
        from app.core.database import SessionLocal
        from app.mcp import discovery

        status = await discovery.discover_and_register()
        if status["state"] == "connected":
            with SessionLocal() as db:
                discovery.sync_mcp_tool_rows(db)
    except Exception as exc:  # noqa: BLE001 - never block startup
        logger.warning("MCP discovery skipped: %s", exc)


async def _log_requests(request: Request, call_next):
    """Access log: method, path, status and duration for every request."""
    import time

    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "%s %s -> 500 (%.0fms)",
            request.method,
            request.url.path,
            (time.perf_counter() - started) * 1000,
        )
        raise
    duration_ms = (time.perf_counter() - started) * 1000
    # Skip the noisy polling endpoints at INFO level.
    if request.url.path not in ("/api/v1/health",):
        logger.info(
            "%s %s -> %d (%.0fms)",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
    return response


def create_app() -> FastAPI:
    app = FastAPI(
        title=f"{settings.PROJECT_NAME} API",
        version=__version__,
        description=(
            "AI 智能知识库与 Agent 工作流平台 后端 API。"
            " Phase 1: 基础设施 + 认证 + Dashboard；"
            " Phase 2: 知识库 CRUD + 文档上传解析 + Celery 异步任务；"
            " Phase 3: Embedding + pgvector 向量检索 + Reranker + RAG 问答与来源引用；"
            " Phase 4: SSE 流式对话 + 对话历史 + Markdown；"
            " Phase 5: Agent 工具调用 + 数据分析沙箱。"
        ),
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    # CORS — frontend dev server in Vite talks to FastAPI directly.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- Phase 7 hardening: request logging + global error handling ------
    app.middleware("http")(_log_requests)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        """Last-resort handler: log with the traceback, answer a clean 500.

        Without this an unexpected error inside a non-HTTPException path
        would bubble a raw traceback to some clients; API consumers always
        get the same JSON error shape as the rest of the API.
        """
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=500,
            content={"detail": "服务器内部错误，请稍后重试或查看服务端日志"},
        )

    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    @app.get("/", include_in_schema=False)
    def root():
        return {
            "name": settings.PROJECT_NAME,
            "version": __version__,
            "docs": "/api/docs",
            "api": settings.API_V1_PREFIX,
        }

    # ---- Phase 6: the self-hosted MCP server (JSON-RPC over HTTP) --------
    if settings.MCP_ENABLED:
        from app.mcp import server as mcp_server
        from app.mcp.protocol import handle_mcp_request

        @app.post("/api/mcp", include_in_schema=True, tags=["mcp-server"])
        async def mcp_endpoint(request: Request):
            """MCP streamable-HTTP endpoint (initialize/tools/list/tools/call)."""
            return await handle_mcp_request(
                request, mcp_server.list_tools, mcp_server.call_tool
            )

    return app


app = create_app()