"""Aggregate router for v1 endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    agents,
    auth,
    chat,
    dashboard,
    documents,
    health,
    knowledge_bases,
    mcp,
    rag,
    tasks,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(dashboard.router)
api_router.include_router(knowledge_bases.router)
# Document routes are nested under /knowledge-bases/{kb_id}/documents, so they
# are registered after the KB router to keep path resolution unambiguous.
api_router.include_router(documents.router)
# Phase 3 RAG routes (/search, /query) hang off the same /knowledge-bases
# prefix and must come after the document routes for the same reason.
api_router.include_router(rag.router)
api_router.include_router(tasks.router)
api_router.include_router(chat.router)
# Phase 5: agent management, tool registry, data files and charts.
api_router.include_router(agents.router)
# Phase 6: MCP tool management (discovery status / refresh / test calls).
api_router.include_router(mcp.router)
