"""MCP management API: discovery status, refresh, test invocation (Phase 6)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import Field, field_validator

from app.api.deps import CurrentUser, DBSession
from app.mcp import discovery, server as mcp_server
from app.schemas.common import ORMModel

router = APIRouter(prefix="/mcp", tags=["mcp"])


class McpToolInfo(ORMModel):
    name: str
    remote_name: str
    description: str
    input_schema: dict[str, Any]


class McpStatus(ORMModel):
    state: str
    tools: int
    error: Optional[str] = None
    server: Optional[dict[str, Any]] = None


class McpToolsResponse(ORMModel):
    status: McpStatus
    tools: list[McpToolInfo]


class McpCallRequest(ORMModel):
    tool: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool")
    @classmethod
    def _strip_tool(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("tool must not be blank")
        return stripped


class McpCallResponse(ORMModel):
    tool: str
    text: str
    is_error: bool


@router.get(
    "/tools",
    response_model=McpToolsResponse,
    summary="Discovered MCP tools + connection status (discovers on first call)",
)
async def list_mcp_tools(db: DBSession, current_user: CurrentUser) -> McpToolsResponse:
    if not settings_enabled():
        return McpToolsResponse(status=McpStatus(state="disabled", tools=0), tools=[])

    if discovery.get_status()["state"] == "not_discovered":
        await discovery.discover_and_register()
    discovery.sync_mcp_tool_rows(db)

    from app.agent.tools import get_tool_registry

    tools = [
        McpToolInfo(
            name=name,
            remote_name=name[len(discovery.MCP_TOOL_PREFIX):],
            description=tool.spec.description,
            input_schema=tool.spec.parameters,
        )
        for name, tool in sorted(get_tool_registry().items())
        if name.startswith(discovery.MCP_TOOL_PREFIX)
    ]
    return McpToolsResponse(
        status=McpStatus(**discovery.get_status()), tools=tools
    )


@router.post(
    "/refresh",
    response_model=McpToolsResponse,
    summary="Re-run tool discovery against the MCP server",
)
async def refresh_mcp_tools(db: DBSession, current_user: CurrentUser) -> McpToolsResponse:
    if not settings_enabled():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="MCP is disabled"
        )
    status_dict = await discovery.discover_and_register()
    discovery.sync_mcp_tool_rows(db)

    from app.agent.tools import get_tool_registry

    tools = [
        McpToolInfo(
            name=name,
            remote_name=name[len(discovery.MCP_TOOL_PREFIX):],
            description=tool.spec.description,
            input_schema=tool.spec.parameters,
        )
        for name, tool in sorted(get_tool_registry().items())
        if name.startswith(discovery.MCP_TOOL_PREFIX)
    ]
    return McpToolsResponse(status=McpStatus(**status_dict), tools=tools)


@router.post(
    "/call",
    response_model=McpCallResponse,
    summary="Invoke an MCP tool directly (test panel)",
)
async def call_mcp_tool(
    payload: McpCallRequest, current_user: CurrentUser  # noqa: ARG001 - auth gate
) -> McpCallResponse:
    text, is_error = await mcp_server.call_tool(payload.tool, payload.arguments)
    return McpCallResponse(tool=payload.tool, text=text, is_error=is_error)


def settings_enabled() -> bool:
    from app.core.config import settings

    return settings.MCP_ENABLED
