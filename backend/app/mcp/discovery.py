"""Tool discovery: remote MCP tools -> agent-usable proxy tools.

Discovery is the framework's "Tool Discovery" stage: the client asks the MCP
server for its tool list and wraps each descriptor in an
:class:`McpProxyTool`, which plugs straight into the existing agent tool
registry (``tools`` table rows carry ``type='mcp'``). Adding a new tool to
the server needs zero agent-side code — that is the point of MCP.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.agent.tools import ToolResult, ToolSpec, clear_extra_tools, register_extra_tool
from app.core.config import settings
from app.mcp.client import McpClient, McpClientError

logger = logging.getLogger(__name__)

MCP_TOOL_PREFIX = "mcp_"

# Process-wide client (holds the MCP session) + last discovery status.
_client: McpClient | None = None
_last_status: dict[str, Any] = {"state": "not_discovered", "tools": 0, "error": None}


class McpProxyTool:
    """Agent-side wrapper around one remote MCP tool.

    Deliberately NOT a subclass of ``BaseTool``-style ABCs — it duck-types the
    same interface (``spec`` + async ``run(args, ctx)``) so third-party tools
    stay decoupled from the platform's own tool base class.
    """

    def __init__(self, descriptor: dict[str, Any], client: McpClient) -> None:
        self.remote_name = str(descriptor.get("name") or "")
        self.spec = ToolSpec(
            name=f"{MCP_TOOL_PREFIX}{self.remote_name}",
            description=f"[MCP] {descriptor.get('description', '')}".strip(),
            parameters=descriptor.get("inputSchema")
            or {"type": "object", "properties": {}},
        )
        self.client = client

    async def run(self, args: dict[str, Any], ctx: Any = None) -> ToolResult:
        text = await self.client.call_tool(self.remote_name, dict(args))
        summary = text.strip().splitlines()[0][:100] if text.strip() else "（无输出）"
        return ToolResult(summary=summary, detail=text)


async def discover_and_register() -> dict[str, Any]:
    """Connect, discover remote tools, register proxies; returns status."""
    global _client
    try:
        client = McpClient(settings.mcp_server_url)
        server_info = await client.initialize()
        descriptors = await client.list_tools()

        clear_extra_tools()
        for descriptor in descriptors:
            register_extra_tool(McpProxyTool(descriptor, client))

        _client = client
        _last_status.clear()
        _last_status.update(
            {
                "state": "connected",
                "server": server_info,
                "tools": len(descriptors),
                "error": None,
            }
        )
        logger.info(
            "MCP discovery: %d tools from %s", len(descriptors), server_info.get("name")
        )
    except McpClientError as exc:
        _last_status.clear()
        _last_status.update({"state": "unreachable", "tools": 0, "error": str(exc)})
        logger.warning("MCP discovery failed: %s", exc)
    return get_status()


def get_status() -> dict[str, Any]:
    return dict(_last_status)


def get_client() -> McpClient | None:
    return _client


def sync_mcp_tool_rows(db: Session) -> None:
    """Mirror discovered MCP tools into the ``tools`` table (type='mcp')."""
    from app.agent.tools import get_tool_registry
    from app.models.agent import Tool

    existing = {row.name: row for row in db.query(Tool).all()}
    for name, tool in get_tool_registry().items():
        if not name.startswith(MCP_TOOL_PREFIX):
            continue
        row = existing.get(name)
        if row is None:
            db.add(
                Tool(
                    name=name,
                    description=tool.spec.description[:512],
                    type="mcp",
                    enabled=True,
                )
            )
        elif row.type != "mcp":
            row.type = "mcp"
            row.description = tool.spec.description[:512]
    # MCP tools that disappeared from the remote server are disabled, not
    # deleted — agents referencing them keep a visible (inactive) row.
    known = set(get_tool_registry())
    for name, row in existing.items():
        if row.type == "mcp" and name not in known:
            row.enabled = False
    db.commit()
