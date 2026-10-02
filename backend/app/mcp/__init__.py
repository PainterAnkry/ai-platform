"""MCP (Model Context Protocol) integration — Phase 6.

Layout, kept independent from business code per the framework requirement:

* :mod:`app.mcp.protocol` — JSON-RPC 2.0 wire helpers + session store
* :mod:`app.mcp.server`   — the self-hosted MCP server (tools + dispatch)
* :mod:`app.mcp.client`   — an MCP client (initialize / tools/list / tools/call)
* :mod:`app.mcp.discovery`— discovery: remote tools -> agent-usable proxies
"""

from app.mcp.protocol import JSONRPC_VERSION, PROTOCOL_VERSION, McpSession  # noqa: F401
