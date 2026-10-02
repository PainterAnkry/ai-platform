"""Phase 4 chat: conversation CRUD, SSE stream, history, citations."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.chat import Message
from app.models.knowledge import KnowledgeBase
from app.models.user import User


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
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    """Split an SSE body into (event, data) tuples."""
    events: list[tuple[str, dict]] = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event_name = ""
        data = ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data += line[len("data:"):].strip()
        if event_name:
            events.append((event_name, json.loads(data) if data else {}))
    return events


# ---------- conversation CRUD ----------
def test_conversation_crud_and_isolation(api: TestClient, sqlite_session: Session) -> None:
    user = _make_user(sqlite_session, "crud")
    stranger = _make_user(sqlite_session, "stranger")
    headers = _auth(api, user)
    stranger_headers = _auth(api, stranger)

    resp = api.post("/api/v1/conversations", json={"title": "测试会话"}, headers=headers)
    assert resp.status_code == 201, resp.text
    conv = resp.json()
    assert conv["title"] == "测试会话"
    assert conv["knowledge_base_id"] is None

    detail = api.get(f"/api/v1/conversations/{conv['id']}", headers=headers).json()
    assert detail["messages"] == []

    renamed = api.patch(
        f"/api/v1/conversations/{conv['id']}",
        json={"title": "改名了"},
        headers=headers,
    ).json()
    assert renamed["title"] == "改名了"

    listed = api.get("/api/v1/conversations", headers=headers).json()
    assert [c["id"] for c in listed] == [conv["id"]]

    # Strangers get 404, not 403 — existence is not leaked.
    resp = api.get(f"/api/v1/conversations/{conv['id']}", headers=stranger_headers)
    assert resp.status_code == 404
    assert (
        api.delete(f"/api/v1/conversations/{conv['id']}", headers=stranger_headers).status_code
        == 404
    )

    # Unauthenticated.
    assert api.get("/api/v1/conversations").status_code == 401

    # Owner deletes.
    assert api.delete(f"/api/v1/conversations/{conv['id']}", headers=headers).status_code == 204
    assert api.get(f"/api/v1/conversations/{conv['id']}", headers=headers).status_code == 404


def test_create_conversation_with_foreign_kb_404(
    api: TestClient, sqlite_session: Session
) -> None:
    owner = _make_user(sqlite_session, "kbowner")
    user = _make_user(sqlite_session, "nocreator")
    kb = KnowledgeBase(user_id=owner.id, name="别人的库")
    sqlite_session.add(kb)
    sqlite_session.commit()

    resp = api.post(
        "/api/v1/conversations",
        json={"knowledge_base_id": kb.id},
        headers=_auth(api, user),
    )
    assert resp.status_code == 404


# ---------- SSE chat ----------
def test_plain_chat_stream_persists_history(
    api: TestClient, sqlite_session: Session
) -> None:
    user = _make_user(sqlite_session, "streamer")
    headers = _auth(api, user)
    conv = api.post("/api/v1/conversations", json={}, headers=headers).json()

    with api.stream(
        "POST",
        f"/api/v1/conversations/{conv['id']}/chat",
        json={"content": "你好，介绍一下你自己"},
        headers=headers,
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(chunk for chunk in response.iter_text())

    events = _parse_sse(body)
    names = [name for name, _ in events]
    assert names[0] == "meta"
    assert names[-1] == "done"
    assert "sources" not in names, "plain chat must not emit RAG sources"
    answer = "".join(data["text"] for name, data in events if name == "delta")
    assert answer, "stream must produce text deltas"
    assert "mock" in answer.lower(), "mock LLM output must be labelled"

    detail = api.get(f"/api/v1/conversations/{conv['id']}", headers=headers).json()
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant"]
    assert detail["messages"][0]["content"] == "你好，介绍一下你自己"
    assert detail["messages"][1]["content"] == answer
    assert detail["messages"][1]["sources"] is None
    assert detail["title"].startswith("你好"), "auto-title from first message"


def test_rag_chat_stream_emits_sources(
    api: TestClient, sqlite_session: Session
) -> None:
    from app.models.knowledge import Document, DocumentChunk
    from app.rag.embeddings import MockEmbeddingProvider

    user = _make_user(sqlite_session, "ragchat")
    kb = KnowledgeBase(user_id=user.id, name="对话知识库")
    sqlite_session.add(kb)
    sqlite_session.commit()
    doc = Document(
        knowledge_base_id=kb.id, user_id=user.id, filename="说明.pdf",
        file_type="pdf", file_size=10, storage_path=f"{user.id}/x.pdf",
        status="completed", chunk_count=1,
    )
    sqlite_session.add(doc)
    sqlite_session.commit()
    provider = MockEmbeddingProvider()
    content = "平台的退款政策是七天内无理由全额退款。"
    sqlite_session.add(
        DocumentChunk(
            document_id=doc.id, knowledge_base_id=kb.id, chunk_index=0,
            content=content, page_number=2, token_estimate=10,
            embedding=provider.embed_query(content),
        )
    )
    sqlite_session.commit()

    headers = _auth(api, user)
    conv = api.post(
        "/api/v1/conversations",
        json={"knowledge_base_id": kb.id},
        headers=headers,
    ).json()

    with api.stream(
        "POST",
        f"/api/v1/conversations/{conv['id']}/chat",
        json={"content": "退款政策是什么"},
        headers=headers,
    ) as response:
        assert response.status_code == 200
        body = "".join(chunk for chunk in response.iter_text())

    events = _parse_sse(body)
    names = [name for name, _ in events]
    assert "sources" in names
    sources = next(data for name, data in events if name == "sources")["sources"]
    assert sources and sources[0]["filename"] == "说明.pdf"
    assert sources[0]["page_number"] == 2

    answer = "".join(data["text"] for name, data in events if name == "delta")
    assert "退款" in answer, "grounded answer must quote the retrieved content"

    detail = api.get(f"/api/v1/conversations/{conv['id']}", headers=headers).json()
    assistant = detail["messages"][-1]
    assert assistant["sources"] and assistant["sources"][0]["page_number"] == 2


def test_chat_rejects_blank_and_foreign(
    api: TestClient, sqlite_session: Session
) -> None:
    user = _make_user(sqlite_session, "guard")
    stranger = _make_user(sqlite_session, "guard2")
    headers = _auth(api, user)
    stranger_headers = _auth(api, stranger)

    conv = api.post("/api/v1/conversations", json={}, headers=headers).json()

    resp = api.post(
        f"/api/v1/conversations/{conv['id']}/chat",
        json={"content": "   "},
        headers=headers,
    )
    assert resp.status_code == 422

    resp = api.post(
        f"/api/v1/conversations/{conv['id']}/chat",
        json={"content": "hi"},
        headers=stranger_headers,
    )
    assert resp.status_code == 404

    # Nothing was persisted by the failed attempts.
    assert sqlite_session.query(Message).count() == 0


def test_dashboard_counts_chats(api: TestClient, sqlite_session: Session) -> None:
    user = _make_user(sqlite_session, "dash")
    headers = _auth(api, user)
    conv = api.post("/api/v1/conversations", json={}, headers=headers).json()
    with api.stream(
        "POST",
        f"/api/v1/conversations/{conv['id']}/chat",
        json={"content": "一句话介绍你自己"},
        headers=headers,
    ) as response:
        assert response.status_code == 200

    dash = api.get("/api/v1/dashboard", headers=headers).json()
    assert dash["stats"]["conversation_count"] == 1
    assert dash["stats"]["ai_call_count"] == 1
    trend = dash["ai_call_trend"]["points"]
    assert sum(p["value"] for p in trend) == 1
