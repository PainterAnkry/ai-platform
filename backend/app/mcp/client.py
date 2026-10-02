"""MCP client: initialize handshake, tool discovery and tool execution.

Speaks the same JSON-RPC subset as :mod:`app.mcp.protocol` over plain HTTP,
so it can talk to the self-hosted server or any compatible MCP endpoint
(``settings.MCP_SERVER_URL``).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.mcp.protocol import SESSION_HEADER

logger = logging.getLogger(__name__)


class McpClientError(RuntimeError):
    """Raised when the MCP server is unreachable or misbehaves."""


class McpClient:
    """One client instance == one MCP session."""

    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session_id: str | None = None
        self.server_info: dict[str, Any] = {}

    # ---------- low-level ----------
    async def _post(
        self, payload: dict[str, Any], *, expect_body: bool = True
    ) -> tuple[dict[str, Any] | None, dict[str, str]]:
        """POST one JSON-RPC message; returns (result, response_headers)."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                headers = (
                    {SESSION_HEADER: self.session_id} if self.session_id else {}
                )
                response = await client.post(
                    self.base_url, json=payload, headers=headers
                )
        except httpx.HTTPError as exc:
            raise McpClientError(f"MCP server unreachable: {exc}") from exc

        session = response.headers.get(SESSION_HEADER)
        if session:
            self.session_id = session

        if not expect_body or response.status_code == 202:
            return None, dict(response.headers)

        body = response.json()
        if "error" in body:
            raise McpClientError(
                f"MCP error {body['error'].get('code')}: "
                f"{body['error'].get('message')}"
            )
        return body.get("result"), dict(response.headers)

    # ---------- protocol ----------
    async def initialize(self) -> dict[str, Any]:
        """Open a session: initialize -> notifications/initialized."""
        result, _ = await self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "ai-platform-agent", "version": "1.0"},
                },
            }
        )
        if not result:
            raise McpClientError("MCP initialize returned no result")
        self.server_info = result.get("serverInfo") or {}
        await self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            expect_body=False,
        )
        return self.server_info

    async def list_tools(self) -> list[dict[str, Any]]:
        result, _ = await self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        if not result:
            raise McpClientError("MCP tools/list returned no result")
        return list(result.get("tools") or [])

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a remote tool; returns its text content.

        ``isError: true`` responses raise :class:`McpClientError` — the proxy
        tool converts that into a failed observation for the agent loop.
        """
        result, _ = await self._post(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        if not result:
            raise McpClientError("MCP tools/call returned no result")
        if result.get("isError"):
            content = result.get("content") or []
            text = content[0].get("text", "工具执行失败") if content else "工具执行失败"
            raise McpClientError(text)
        parts = [
            str(block.get("text", ""))
            for block in result.get("content") or []
            if block.get("type") == "text"
        ]
        return "\n".join(part for part in parts if part)
