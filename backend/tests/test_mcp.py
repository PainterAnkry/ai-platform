"""Phase 6 MCP: protocol handshake, server tools, client, discovery."""

from __future__ import annotations

import asyncio
import ast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.mcp import discovery
from app.mcp.client import McpClient, McpClientError
from app.mcp.protocol import reset_sessions
from app.mcp import server as mcp_server
from app.models.knowledge import Document, DocumentChunk, KnowledgeBase
from app.models.user import User
from app.rag.embeddings import MockEmbeddingProvider


@pytest.fixture(autouse=True)
def _clean_sessions():
    reset_sessions()
    discovery.clear_extra_tools()
    discovery._last_status.update({"state": "not_discovered", "tools": 0, "error": None})
    yield
    reset_sessions()
    discovery.clear_extra_tools()


def test_server_calculator_and_unknown_tool() -> None:
    text, is_error = asyncio.run(mcp_server.call_tool("calculator", {"expression": "2+3*4"}))
    assert is_error is False
    assert "= 14" in text

    text, is_error = asyncio.run(mcp_server.call_tool("nope", {}))
    assert is_error is True
    assert "未知工具" in text


def test_server_http_request_blocks_private_targets() -> None:
    for url in (
        "http://127.0.0.1:8000/api/v1/health",
        "http://localhost/x",
        "http://192.168.1.1/admin",
        "ftp://example.com/file",
    ):
        text, is_error = asyncio.run(mcp_server.call_tool("http_request", {"url": url}))
        assert is_error is True, url
        assert "拒绝" in text or "仅允许" in text


def test_server_knowledge_search(sqlite_session: Session) -> None:
    from app.core import database as database_mod

    user = User(
        username="mcpuser",
        email="mcp@example.com",
        hashed_password=hash_password("x"),
    )
    sqlite_session.add(user)
    sqlite_session.commit()
    kb = KnowledgeBase(user_id=user.id, name="mcp 库")
    sqlite_session.add(kb)
    sqlite_session.commit()
    doc = Document(
        knowledge_base_id=kb.id, user_id=user.id, filename="faq.pdf",
        file_type="pdf", file_size=10, storage_path="x", status="completed",
        chunk_count=1,
    )
    sqlite_session.add(doc)
    sqlite_session.commit()
    provider = MockEmbeddingProvider()
    content = "发票开具后十五个工作日内到账。"
    sqlite_session.add(
        DocumentChunk(
            document_id=doc.id, knowledge_base_id=kb.id, chunk_index=0,
            content=content, page_number=4, token_estimate=10,
            embedding=provider.embed_query(content),
        )
    )
    sqlite_session.commit()

    # The MCP server opens its own session from SessionLocal — point it at
    # the test database.
    original = database_mod.SessionLocal
    database_mod.SessionLocal = lambda: sqlite_session  # type: ignore[assignment]
    try:
        text, is_error = asyncio.run(
            mcp_server.call_tool(
                "knowledge_search", {"kb_id": kb.id, "query": "发票多久到账"}
            )
        )
    finally:
        database_mod.SessionLocal = original

    assert is_error is False
    assert "发票" in text and "第 4 页" in text

    text, is_error = asyncio.run(
        mcp_server.call_tool(
            "knowledge_search", {"kb_id": kb.id, "query": "完全无关的内容 xyz"}
        )
    )
    # Retrieval has no relevance threshold: with a single chunk it always
    # returns something — assert it degrades gracefully either way.
    assert is_error is False and text.strip()


def test_server_python_analysis_sandbox() -> None:
    code = "import pandas as pd\nprint(len(df))\nprint(df['v'].sum())"
    text, is_error = asyncio.run(
        mcp_server.call_tool(
            "python_analysis", {"code": code, "csv_text": "v\n1\n2\n3\n"}
        )
    )
    assert is_error is False
    assert "3" in text and "6" in text

    text, is_error = asyncio.run(
        mcp_server.call_tool("python_analysis", {"code": "import os\nprint(os.getcwd())"})
    )
    assert is_error is True and "拒绝" in text


def test_client_roundtrip_against_mounted_server() -> None:
    """Full protocol loop against the mounted server via the ASGI transport."""

    import httpx
    from app.main import app

    async def _run():
        transport = httpx.ASGITransport(app=app)
        client = McpClient("http://testserver/api/mcp")

        async def patched_post(self, payload, *, expect_body=True):
            response = await transport.handle_async_request(
                httpx.Request(
                    "POST",
                    "http://testserver/api/mcp",
                    json=payload,
                    headers=(
                        {"Mcp-Session-Id": self.session_id}
                        if self.session_id
                        else {}
                    ),
                )
            )
            session = response.headers.get("Mcp-Session-Id")
            if session:
                self.session_id = session
            if not expect_body or response.status_code == 202:
                return None, dict(response.headers)
            await response.aread()
            body = response.json()
            if "error" in body:
                raise McpClientError(str(body["error"]))
            return body.get("result"), dict(response.headers)

        original_post = McpClient._post
        McpClient._post = patched_post  # type: ignore[method-assign]
        try:
            info = await client.initialize()
            tools = await client.list_tools()
            text = await client.call_tool("calculator", {"expression": "6*7"})
        finally:
            McpClient._post = original_post  # type: ignore[method-assign]
        return info, tools, text

    info, tools, text = asyncio.run(_run())
    assert info["name"] == "ai-platform-mcp"
    remote_names = {t["name"] for t in tools}
    assert {"calculator", "knowledge_search", "weather", "http_request", "python_analysis"} <= remote_names
    assert "42" in text


def test_discovery_registers_proxy_tools() -> None:
    """Discovery wraps remote descriptors into mcp_* agent tools."""
    from app.agent.tools import get_tool_registry

    client = McpClient("http://unused/")

    async def fake_call(name, arguments):
        return f"计算结果：{arguments.get('expression')} = 42"

    client.call_tool = fake_call  # type: ignore[method-assign]
    status = asyncio.run(_discover_with(client))
    assert status["state"] == "connected"
    assert status["tools"] >= 5

    registry = get_tool_registry()
    assert "mcp_calculator" in registry
    proxy = registry["mcp_calculator"]
    assert proxy.spec.name == "mcp_calculator"
    assert proxy.spec.description.startswith("[MCP]")


    result = asyncio.run(proxy.run({"expression": "6*7"}, None))
    assert "42" in result.detail


async def _discover_with(client: McpClient):
    """Run discovery against a stubbed client."""
    from app.mcp import discovery as disc
    from app.mcp import server as srv

    disc._client = None

    real_initialize = McpClient.initialize
    real_list = McpClient.list_tools

    async def fake_initialize(self):
        self.server_info = {"name": "stub"}
        return self.server_info

    async def fake_list(self):
        return srv.list_tools()

    McpClient.initialize = fake_initialize  # type: ignore[method-assign]
    McpClient.list_tools = fake_list  # type: ignore[method-assign]
    try:
        # Replace the module-level client factory by monkey-driving internals.
        saved_client_cls = disc.McpClient
        disc.McpClient = lambda url: client  # type: ignore[assignment]
        try:
            return await disc.discover_and_register()
        finally:
            disc.McpClient = saved_client_cls
    finally:
        McpClient.initialize = real_initialize  # type: ignore[method-assign]
        McpClient.list_tools = real_list  # type: ignore[method-assign]


def test_mcp_api_and_agent_flow(api: TestClient, sqlite_session: Session) -> None:
    """Discovery via API, then an agent chat that routes through mcp_calculator."""
    from app.rag.llm import get_llm_provider

    get_llm_provider.cache_clear()
    user = User(
        username="e6api",
        email="e6api@example.com",
        hashed_password=hash_password("password123"),
    )
    sqlite_session.add(user)
    sqlite_session.commit()
    resp = api.post(
        "/api/v1/auth/login",
        json={"username": "e6api", "password": "password123"},
    )
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    # Drive discovery through the stubbed path (no real HTTP to self in
    # tests); the stub's call_tool returns text like a real client.
    class StubClient(McpClient):
        async def initialize(self):
            self.server_info = {"name": "stub"}
            return self.server_info

        async def list_tools(self):
            from app.mcp import server

            return server.list_tools()

        async def call_tool(self, name, arguments):
            from app.agent.tools import _safe_eval

            value = _safe_eval(
                ast.parse(arguments.get("expression", "0"), mode="eval")
            )
            return f"计算结果：{arguments.get('expression')} = {value:g}"

    asyncio.run(_discover_with(StubClient("http://unused/")))

    status_resp = api.get("/api/v1/mcp/tools", headers=headers)
    assert status_resp.status_code == 200
    payload = status_resp.json()
    assert payload["status"]["state"] == "connected"
    names = {t["name"] for t in payload["tools"]}
    assert "mcp_calculator" in names and "mcp_weather" in names

    # Direct test invocation.
    call_resp = api.post(
        "/api/v1/mcp/call",
        json={"tool": "calculator", "arguments": {"expression": "10/4"}},
        headers=headers,
    )
    assert call_resp.status_code == 200
    assert "2.5" in call_resp.json()["text"]

    # An agent with the mcp calculator routes the arithmetic question to it.
    agent = api.post(
        "/api/v1/agents",
        json={"name": "MCP 助手", "tools": ["mcp_calculator"]},
        headers=headers,
    ).json()
    conv = api.post(
        "/api/v1/conversations",
        json={"agent_id": agent["id"]},
        headers=headers,
    ).json()

    with api.stream(
        "POST",
        f"/api/v1/conversations/{conv['id']}/chat",
        json={"content": "计算 10/4 是多少"},
        headers=headers,
    ) as response:
        body = "".join(chunk for chunk in response.iter_text())

    assert "mcp_calculator" in body, "step timeline must show the MCP tool call"
    assert "2.5" in body
