"""MCP wire protocol: JSON-RPC 2.0 over streamable HTTP (POST).

Implements the subset of the MCP spec this project needs, faithful to the
real protocol shape so a standard client could talk to it:

* ``initialize``        -> capabilities + ``Mcp-Session-Id`` response header
* ``notifications/initialized`` (no id) -> 202, empty body
* ``tools/list``        -> {"tools": [{name, description, inputSchema}]}
* ``tools/call``        -> {"content": [{type: "text", text}], "isError"}
* unknown method        -> JSON-RPC error -32601
* missing/unknown session -> JSON-RPC error + HTTP 404

No third-party MCP SDK: the protocol subset is small, and staying
dependency-free avoids version conflicts with the pinned FastAPI stack.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from fastapi import Request
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

JSONRPC_VERSION = "2.0"
# Protocol version this implementation speaks; clients negotiate down if needed.
PROTOCOL_VERSION = "2025-06-18"
SESSION_HEADER = "Mcp-Session-Id"

# JSON-RPC error codes used below.
ERR_PARSE = -32700
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603


@dataclass
class McpSession:
    """One MCP client session (server-side state)."""

    id: str
    client_info: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<McpSession {self.id[:8]}>"


# In-process session store. The server lives inside the API app, so process
# memory is the natural scope; a standalone deployment would move this to
# Redis (Phase 7 hardening territory).
_SESSIONS: dict[str, McpSession] = {}


def _jsonrpc_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def reset_sessions() -> None:
    """Test helper: drop every session."""
    _SESSIONS.clear()


async def handle_mcp_request(
    request: Request,
    tool_names: Callable[[], list[dict[str, Any]]],
    call_tool: Callable[[str, dict[str, Any]], Awaitable[tuple[str, bool]]],
) -> Response:
    """Entry point for POST /api/mcp.

    ``tool_names`` returns the MCP tool descriptors (for tools/list);
    ``call_tool(name, arguments)`` executes one and returns
    ``(text, is_error)``.
    """
    try:
        body: dict[str, Any] = await request.json()
    except Exception:  # noqa: BLE001 - malformed JSON
        return JSONResponse(_jsonrpc_error(None, ERR_PARSE, "Parse error"), status_code=400)

    method: str = body.get("method") or ""
    request_id = body.get("id")  # None => notification
    session_id = request.headers.get(SESSION_HEADER)

    # ---- initialize: create a session ------------------------------------
    if method == "initialize":
        params = body.get("params") or {}
        session = McpSession(id=uuid.uuid4().hex, client_info=params.get("clientInfo") or {})
        _SESSIONS[session.id] = session
        logger.info("MCP session opened for client %s", session.client_info.get("name", "?"))
        return JSONResponse(
            _jsonrpc_result(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "ai-platform-mcp", "version": "1.0.0"},
                },
            ),
            headers={SESSION_HEADER: session.id},
        )

    # ---- everything else requires a valid session ------------------------
    session = _SESSIONS.get(session_id or "")
    if session is None:
        return JSONResponse(
            _jsonrpc_error(request_id, ERR_INTERNAL, "Session not found"),
            status_code=404,
        )

    # ---- notifications carry no id and get no body back ------------------
    if request_id is None:
        if method == "notifications/initialized":
            return Response(status_code=202)
        return Response(status_code=202)  # ignore unknown notifications

    if method == "tools/list":
        return JSONResponse(_jsonrpc_result(request_id, {"tools": tool_names()}))

    if method == "tools/call":
        params = body.get("params") or {}
        name = params.get("name") or ""
        arguments = params.get("arguments") or {}
        try:
            text, is_error = await call_tool(name, arguments)
        except Exception as exc:  # noqa: BLE001 - reported as a tool error
            logger.exception("MCP tool %s crashed", name)
            text, is_error = f"工具内部错误：{exc}", True
        return JSONResponse(
            _jsonrpc_result(
                request_id,
                {
                    "content": [{"type": "text", "text": text}],
                    "isError": is_error,
                },
            )
        )

    return JSONResponse(
        _jsonrpc_error(request_id, ERR_METHOD_NOT_FOUND, f"Unknown method: {method}"),
        status_code=404,
    )
