"""Phase 5 agent: tools, sandbox, executor loop, API and agent chat."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.agent.executor import AgentRunResult, run_agent_stream
from app.agent.sandbox import run_analysis
from app.agent.tools import (
    CalculatorTool,
    KnowledgeSearchTool,
    ToolContext,
    ToolError,
    get_tool_registry,
)
from app.core.config import settings
from app.core.security import hash_password
from app.models.agent import DataFile
from app.models.knowledge import Document, DocumentChunk, KnowledgeBase
from app.models.user import User
from app.rag.embeddings import MockEmbeddingProvider
from app.rag.llm import get_llm_provider


def _make_user(db: Session, name: str) -> User:
    user = User(
        username=name,
        email=f"{name}@example.com",
        hashed_password=hash_password("password123"),
    )
    db.add(user)
    db.commit()
    return user


def _auth(client: TestClient, user: User) -> dict[str, str]:
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": user.username, "password": "password123"},
    )
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _ctx(db: Session, user: User, kb_id: int | None = None) -> ToolContext:
    return ToolContext(db=db, user_id=user.id, knowledge_base_id=kb_id)


def _run(coro):
    """Collect an async generator into a list."""

    async def _collect():
        return [item async for item in coro]

    return asyncio.run(_collect())


# ---------- calculator ----------
def test_calculator_evaluates_and_blocks() -> None:
    tool = CalculatorTool()

    result = asyncio.run(tool.run({"expression": "(1200+980)*1.12"}, None))
    assert result.summary.endswith("= 2441.6")

    with pytest.raises(ToolError):
        asyncio.run(tool.run({"expression": "__import__('os').system('x')"}, None))
    with pytest.raises(ToolError):
        asyncio.run(tool.run({"expression": "os.path"}, None))
    with pytest.raises(ToolError):
        asyncio.run(tool.run({"expression": ""}, None))


# ---------- knowledge_search ----------
def test_knowledge_search_with_and_without_kb(sqlite_session: Session) -> None:
    user = _make_user(sqlite_session, "tooluser")
    kb = KnowledgeBase(user_id=user.id, name="工具库")
    sqlite_session.add(kb)
    sqlite_session.commit()
    doc = Document(
        knowledge_base_id=kb.id, user_id=user.id, filename="政策.pdf",
        file_type="pdf", file_size=10, storage_path="x", status="completed",
        chunk_count=1,
    )
    sqlite_session.add(doc)
    sqlite_session.commit()
    provider = MockEmbeddingProvider()
    content = "年假规定：入职满一年可享受五天带薪年假。"
    sqlite_session.add(
        DocumentChunk(
            document_id=doc.id, knowledge_base_id=kb.id, chunk_index=0,
            content=content, page_number=1, token_estimate=10,
            embedding=provider.embed_query(content),
        )
    )
    sqlite_session.commit()

    tool = KnowledgeSearchTool()
    result = asyncio.run(tool.run({"query": "年假有多少天"}, _ctx(sqlite_session, user, kb.id)))
    assert "检索到" in result.summary
    assert "年假" in result.detail
    assert result.sources and result.sources[0]["filename"] == "政策.pdf"

    no_kb = asyncio.run(
        tool.run({"query": "年假"}, _ctx(sqlite_session, user, None))
    )
    assert "没有绑定知识库" in no_kb.detail


# ---------- sandbox ----------
@pytest.fixture()
def upload_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(root), raising=False)
    return root


CSV_BODY = (
    "月份,销售额\n"
    "2024-01,100\n2024-02,120\n2024-03,110\n"
    "2024-04,130\n2024-05,900\n2024-06,105\n"
)


def test_sandbox_analysis_produces_summary_and_chart(
    sqlite_session: Session, upload_root: Path
) -> None:
    user = _make_user(sqlite_session, "analyst")
    path = upload_root / "sales.csv"
    path.write_text(CSV_BODY, encoding="utf-8")
    data_file = DataFile(
        user_id=user.id, filename="sales.csv", file_type="csv",
        file_size=path.stat().st_size, storage_path=str(path),
    )
    sqlite_session.add(data_file)
    sqlite_session.commit()

    outcome = run_analysis(data_file, "分析各月销售趋势并找出异常月份", sqlite_session)
    assert "按月汇总" in outcome.summary
    assert "异常月份" in outcome.summary
    assert outcome.chart_url and outcome.chart_url.startswith("/api/v1/agents/charts/")
    chart_path = outcome.chart_url.rsplit("/", 1)[-1]
    assert (upload_root / "analysis" / chart_path).exists()


def test_sandbox_rejects_forbidden_imports(
    sqlite_session: Session, upload_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agent import sandbox as sandbox_mod

    user = _make_user(sqlite_session, "sandboxed")
    path = upload_root / "sales.csv"
    path.write_text(CSV_BODY, encoding="utf-8")
    data_file = DataFile(
        user_id=user.id, filename="sales.csv", file_type="csv",
        file_size=path.stat().st_size, storage_path=str(path),
    )
    sqlite_session.add(data_file)
    sqlite_session.commit()

    monkeypatch.setattr(sandbox_mod, "_analysis_code", lambda df, q: (
        "import os\nprint(os.listdir('.'))"
    ))
    with pytest.raises(ToolError, match="不允许导入"):
        run_analysis(data_file, "任何问题", sqlite_session)


# ---------- executor loop with mock provider ----------
def test_executor_calculator_flow(sqlite_session: Session) -> None:
    get_llm_provider.cache_clear()
    user = _make_user(sqlite_session, "loopuser")
    result = AgentRunResult()
    events = _run(
        run_agent_stream(
            _ctx(sqlite_session, user),
            question="请帮我计算 (1200+980)*1.12 等于多少",
            history=[],
            env={"has_kb": False, "data_files": []},
            result=result,
        )
    )
    kinds = [e.event for e in events]
    # The executor yields step/delta events; the chat service wraps them with
    # the final `done` event after persisting the assistant message.
    assert kinds[0] == "step" and kinds[-1] == "delta"
    steps = [e.data for e in events if e.event == "step"]
    assert any(s["type"] == "tool_call" and s["tool"] == "calculator" for s in steps)
    assert any(s["type"] == "tool_result" for s in steps)
    assert "2441.6" in result.answer
    assert result.model == "mock"


def test_executor_knowledge_flow_with_sources(sqlite_session: Session) -> None:
    get_llm_provider.cache_clear()
    user = _make_user(sqlite_session, "loopkb")
    kb = KnowledgeBase(user_id=user.id, name="循环库")
    sqlite_session.add(kb)
    sqlite_session.commit()
    doc = Document(
        knowledge_base_id=kb.id, user_id=user.id, filename="手册.pdf",
        file_type="pdf", file_size=10, storage_path="x", status="completed",
        chunk_count=1,
    )
    sqlite_session.add(doc)
    sqlite_session.commit()
    provider = MockEmbeddingProvider()
    content = "本产品保修期为两年，包含所有硬件故障。"
    sqlite_session.add(
        DocumentChunk(
            document_id=doc.id, knowledge_base_id=kb.id, chunk_index=0,
            content=content, page_number=3, token_estimate=10,
            embedding=provider.embed_query(content),
        )
    )
    sqlite_session.commit()

    result = AgentRunResult()
    events = _run(
        run_agent_stream(
            _ctx(sqlite_session, user, kb.id),
            question="保修期是多久",
            history=[],
            env={"has_kb": True, "data_files": []},
            result=result,
        )
    )
    steps = [e.data for e in events if e.event == "step"]
    assert any(s["tool"] == "knowledge_search" for s in steps)
    assert result.sources and result.sources[0]["page_number"] == 3
    assert "保修" in result.answer


# ---------- API ----------
def test_agent_crud_and_tools(api: TestClient, sqlite_session: Session) -> None:
    user = _make_user(sqlite_session, "agentowner")
    stranger = _make_user(sqlite_session, "agentother")
    headers = _auth(api, user)
    stranger_headers = _auth(api, stranger)

    # Tool registry rows synced and listed.
    tools = api.get("/api/v1/agents/tools", headers=headers).json()
    names = {t["name"] for t in tools}
    assert {"knowledge_search", "calculator", "data_analysis"} <= names

    resp = api.post(
        "/api/v1/agents",
        json={"name": "数据分析师", "system_prompt": "你擅长数据分析", "tools": ["calculator"]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    agent = resp.json()
    assert agent["tools"] == ["calculator"]

    # Unknown tool rejected.
    resp = api.post(
        "/api/v1/agents", json={"name": "坏工具", "tools": ["nope"]}, headers=headers
    )
    assert resp.status_code == 400

    # Isolation.
    assert (
        api.get(f"/api/v1/agents/{agent['id']}", headers=stranger_headers).status_code
        == 404
    )

    # Update + delete.
    renamed = api.patch(
        f"/api/v1/agents/{agent['id']}",
        json={"name": "分析师Pro"},
        headers=headers,
    ).json()
    assert renamed["name"] == "分析师Pro"
    assert api.delete(f"/api/v1/agents/{agent['id']}", headers=headers).status_code == 204


def test_data_file_upload_and_agent_chart_flow(
    api: TestClient, sqlite_session: Session, upload_root: Path
) -> None:
    user = _make_user(sqlite_session, "dataowner")
    headers = _auth(api, user)

    # Upload a CSV through the API.
    resp = api.post(
        "/api/v1/agents/data-files",
        files={"file": ("sales.csv", CSV_BODY.encode("utf-8"), "text/csv")},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    data_file = resp.json()
    assert data_file["row_count"] == 6
    assert data_file["column_names"] == ["月份", "销售额"]

    # Unsupported extension rejected.
    resp = api.post(
        "/api/v1/agents/data-files",
        files={"file": ("evil.exe", b"MZ", "application/octet-stream")},
        headers=headers,
    )
    assert resp.status_code == 400

    # Create an agent with all tools and a bound conversation.
    agent = api.post(
        "/api/v1/agents", json={"name": "全能助手"}, headers=headers
    ).json()
    conv = api.post(
        "/api/v1/conversations",
        json={"agent_id": agent["id"]},
        headers=headers,
    ).json()

    with api.stream(
        "POST",
        f"/api/v1/conversations/{conv['id']}/chat",
        json={"content": "分析 sales.csv 的各月销售趋势并找出异常月份"},
        headers=headers,
    ) as response:
        assert response.status_code == 200
        import json as _json

        body = "".join(chunk for chunk in response.iter_text())

    events = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        name, data = "", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if name:
            events.append((name, _json.loads(data) if data else {}))

    kinds = [n for n, _ in events]
    assert "step" in kinds and kinds[-1] == "done"
    steps = [d for n, d in events if n == "step"]
    assert any(s["tool"] == "data_analysis" for s in steps)

    done = events[-1][1]
    attachments = done.get("attachments") or []
    assert attachments and attachments[0]["url"].startswith("/api/v1/agents/charts/")

    # Chart is servable and traversal is blocked. The framework may normalise
    # the encoded slashes before routing (404) or the handler may reject the
    # name (400) — both are safe outcomes.
    chart_name = attachments[0]["url"].rsplit("/", 1)[-1]
    assert api.get(f"/api/v1/agents/charts/{chart_name}").status_code == 200
    assert api.get("/api/v1/agents/charts/..%2F..%2Fsecret.png").status_code in (400, 404)

    # Assistant message persisted the step log.
    detail = api.get(f"/api/v1/conversations/{conv['id']}", headers=headers).json()
    assistant = detail["messages"][-1]
    assert assistant["tool_calls"] and assistant["tool_calls"]["steps"]
    assert "按月汇总" in assistant["content"]
