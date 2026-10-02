"""Ingestion worker pipelines: parse+embed end-to-end, re-embed, guards."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.knowledge import Document, DocumentChunk, KnowledgeBase, Task
from app.models.user import User
from app.schemas.task import TASK_TYPE_DOCUMENT_EMBED
from app.services.document_service import DocumentService
from app.workers import tasks as worker_tasks

DOC_BODY = (
    "第一章 项目背景\n\n"
    "本知识库用于验证 Phase 3 的向量检索能力。\n\n"
    "第二章 技术细节\n\n"
    "pgvector 为 PostgreSQL 提供向量类型与 HNSW 索引，支持余弦距离检索。\n\n"
) + ("系统会自动完成文本清洗、切片与向量化，并在界面展示进度。" * 40)


@pytest.fixture()
def upload_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(root), raising=False)
    return root


def _seed_document(
    db: Session, *, status: str = "pending", chunk_count: int = 0
) -> tuple[User, Document]:
    user = User(username="ingest", email="ingest@example.com", hashed_password="x")
    db.add(user)
    db.commit()
    kb = KnowledgeBase(user_id=user.id, name="管线测试库")
    db.add(kb)
    db.commit()
    doc = Document(
        knowledge_base_id=kb.id,
        user_id=user.id,
        filename="pipeline.txt",
        file_type="txt",
        file_size=10,
        storage_path=f"{user.id}/pipeline.txt",
        status=status,
        chunk_count=chunk_count,
    )
    db.add(doc)
    db.commit()
    return user, doc


def _write_upload(upload_root: Path, user_id: int, doc_id: int) -> Path:
    stored = upload_root / str(user_id) / f"{doc_id}.txt"
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(DOC_BODY.encode("utf-8"))
    return stored


def test_parse_pipeline_embeds_chunks_and_records_provider(
    sqlite_session: Session,
    sqlite_session_factory,
    upload_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, doc = _seed_document(sqlite_session)
    _write_upload(upload_root, user.id, doc.id)
    doc.storage_path = f"{user.id}/{doc.id}.txt"
    sqlite_session.commit()

    task = Task(
        user_id=user.id, document_id=doc.id, type="document.parse",
        payload={"document_id": doc.id},
    )
    sqlite_session.add(task)
    sqlite_session.commit()

    monkeypatch.setattr(worker_tasks, "SessionLocal", sqlite_session_factory)
    outcome = worker_tasks.run_parse_task_inline(task.id)

    assert outcome["status"] == "completed", outcome
    assert outcome["embedding_model"] == "mock:1024"

    sqlite_session.expire_all()
    refreshed_doc = sqlite_session.get(Document, doc.id)
    assert refreshed_doc is not None
    assert refreshed_doc.status == "completed"
    assert refreshed_doc.embedding_model == "mock:1024"
    assert refreshed_doc.chunk_count > 0

    chunks = (
        sqlite_session.query(DocumentChunk)
        .filter(DocumentChunk.document_id == doc.id)
        .all()
    )
    assert len(chunks) == refreshed_doc.chunk_count
    for chunk in chunks:
        assert chunk.embedding is not None
        assert len(chunk.embedding) == settings.EMBEDDING_DIM
        norm = math.sqrt(sum(x * x for x in chunk.embedding))
        assert norm == pytest.approx(1.0, abs=1e-6), "vectors must be unit-length"


def test_reembed_pipeline_fills_missing_vectors(
    sqlite_session: Session, sqlite_session_factory
) -> None:
    user, doc = _seed_document(sqlite_session, status="failed", chunk_count=0)
    doc.status = "completed"  # parse finished but embedding failed earlier
    sqlite_session.add_all(
        [
            DocumentChunk(
                document_id=doc.id, knowledge_base_id=doc.knowledge_base_id,
                chunk_index=0, content="机器学习通过数据训练模型。",
                page_number=1, token_estimate=12,
            ),
            DocumentChunk(
                document_id=doc.id, knowledge_base_id=doc.knowledge_base_id,
                chunk_index=1, content="HNSW 是一种近似最近邻图索引。",
                page_number=1, token_estimate=14,
            ),
        ]
    )
    sqlite_session.commit()
    task = Task(
        user_id=user.id, document_id=doc.id, type=TASK_TYPE_DOCUMENT_EMBED,
        payload={"document_id": doc.id},
    )
    sqlite_session.add(task)
    sqlite_session.commit()

    original_sessionlocal = worker_tasks.SessionLocal
    worker_tasks.SessionLocal = sqlite_session_factory
    try:
        outcome = worker_tasks.run_embed_task_inline(task.id)
    finally:
        worker_tasks.SessionLocal = original_sessionlocal

    assert outcome["status"] == "completed", outcome
    assert outcome["chunk_count"] == 2

    sqlite_session.expire_all()
    refreshed_doc = sqlite_session.get(Document, doc.id)
    assert refreshed_doc is not None
    assert refreshed_doc.status == "completed"
    assert refreshed_doc.embedding_model == "mock:1024"
    assert refreshed_doc.error_message is None

    chunks = (
        sqlite_session.query(DocumentChunk)
        .filter(DocumentChunk.document_id == doc.id)
        .order_by(DocumentChunk.chunk_index)
        .all()
    )
    assert all(chunk.embedding is not None for chunk in chunks)

    refreshed_task = sqlite_session.get(Task, task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == "completed"
    assert (refreshed_task.result or {}).get("embedding_model") == "mock:1024"


def test_reembed_pipeline_fails_cleanly_without_chunks(
    sqlite_session: Session, sqlite_session_factory
) -> None:
    user, doc = _seed_document(sqlite_session, status="completed", chunk_count=0)
    task = Task(
        user_id=user.id, document_id=doc.id, type=TASK_TYPE_DOCUMENT_EMBED,
        payload={"document_id": doc.id},
    )
    sqlite_session.add(task)
    sqlite_session.commit()

    original_sessionlocal = worker_tasks.SessionLocal
    worker_tasks.SessionLocal = sqlite_session_factory
    try:
        outcome = worker_tasks.run_embed_task_inline(task.id)
    finally:
        worker_tasks.SessionLocal = original_sessionlocal

    assert outcome["status"] == "failed"
    assert "no chunks" in outcome["error"]

    sqlite_session.expire_all()
    refreshed_doc = sqlite_session.get(Document, doc.id)
    assert refreshed_doc is not None
    assert refreshed_doc.status == "failed"
    assert refreshed_doc.error_message and "no chunks" in refreshed_doc.error_message

    refreshed_task = sqlite_session.get(Task, task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == "failed"


def test_create_embed_task_guards(sqlite_session: Session) -> None:
    service = DocumentService(sqlite_session)
    user, doc = _seed_document(sqlite_session, status="processing", chunk_count=0)

    # Still processing -> 409
    with pytest.raises(HTTPException) as exc_info:
        service.create_embed_task(doc.id, user.id)
    assert exc_info.value.status_code == 409

    # No chunks -> 400
    doc.status = "completed"
    sqlite_session.commit()
    with pytest.raises(HTTPException) as exc_info:
        service.create_embed_task(doc.id, user.id)
    assert exc_info.value.status_code == 400

    # Someone else's document -> 404
    stranger = User(username="stranger", email="s@example.com", hashed_password="x")
    sqlite_session.add(stranger)
    sqlite_session.commit()
    with pytest.raises(HTTPException) as exc_info:
        service.create_embed_task(doc.id, stranger.id)
    assert exc_info.value.status_code == 404
