"""Dispatch behaviour of the upload -> parse hand-off.

``DocumentService.dispatch_parse`` is the branch that decides between queueing
the parse on Celery and running it inline. Both branches must:

* persist what actually happened (``celery_task_id`` for the queued case), and
* report it to the caller (``queued`` / ``inline``), which the upload response
  and the frontend rely on.

The queued path is otherwise hard to reach in a local preview (no broker), so
the Celery task object is stubbed here; the inline path runs the real pipeline.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.knowledge import Document, KnowledgeBase, Task
from app.models.user import User
from app.schemas.task import TASK_TYPE_DOCUMENT_PARSE
from app.services.document_service import DocumentService
from app.workers import tasks as worker_tasks

CHINESE_BODY = (
    "第一章 概述\n\n本项目演示异步文档解析。\n\n"
) + ("这是用于验证切片数量的一段较长的中文内容。" * 60)


def _seed_pending_upload(
    db: Session, upload_root: Path
) -> tuple[User, Document, Task, Path]:
    """Create a user, knowledge base, stored file, document row and task row."""
    user = User(username="dispatch", email="dispatch@example.com", hashed_password="x")
    db.add(user)
    db.commit()

    kb = KnowledgeBase(user_id=user.id, name="解析测试库")
    db.add(kb)
    db.commit()

    stored = upload_root / str(user.id) / "sample.txt"
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(CHINESE_BODY.encode("utf-8"))

    doc = Document(
        knowledge_base_id=kb.id,
        user_id=user.id,
        filename="sample.txt",
        file_type="txt",
        file_size=stored.stat().st_size,
        storage_path=f"{user.id}/sample.txt",
        status="pending",
        progress=0,
    )
    db.add(doc)
    db.commit()

    task = Task(
        user_id=user.id,
        document_id=doc.id,
        type=TASK_TYPE_DOCUMENT_PARSE,
        payload={"document_id": doc.id, "storage_path": doc.storage_path, "file_type": "txt"},
    )
    db.add(task)
    db.commit()
    return user, doc, task, stored


@pytest.fixture()
def upload_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(root), raising=False)
    return root


def test_broker_available_queues_and_records_celery_task_id(
    sqlite_session: Session, upload_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, doc, task, _ = _seed_pending_upload(sqlite_session, upload_root)
    service = DocumentService(sqlite_session)

    published: list[int] = []

    def fake_delay(task_row_id: int) -> SimpleNamespace:
        published.append(task_row_id)
        return SimpleNamespace(id="celery-task-abc")

    monkeypatch.setattr(
        worker_tasks, "parse_document_task", SimpleNamespace(delay=fake_delay)
    )

    outcome = asyncio.run(service.dispatch_parse(task, broker_ok=True))

    assert outcome == "queued"
    assert published == [task.id]
    refreshed = sqlite_session.get(Task, task.id)
    assert refreshed is not None and refreshed.celery_task_id == "celery-task-abc"
    # Nothing parsed in-process: the worker owns it now.
    assert sqlite_session.get(Document, doc.id).status == "pending"


def test_no_broker_runs_inline_and_completes_document(
    sqlite_session: Session,
    sqlite_session_factory,
    upload_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, doc, task, _ = _seed_pending_upload(sqlite_session, upload_root)
    service = DocumentService(sqlite_session)

    def explode(_task_row_id: int):  # pragma: no cover - must not be called
        raise AssertionError("Celery must not be published when the broker is down")

    monkeypatch.setattr(
        worker_tasks, "parse_document_task", SimpleNamespace(delay=explode)
    )
    # The inline runner deliberately opens its own session; point that factory
    # at the test database instead of the real (PostgreSQL) engine.
    monkeypatch.setattr(worker_tasks, "SessionLocal", sqlite_session_factory)

    outcome = asyncio.run(service.dispatch_parse(task, broker_ok=False))

    assert outcome == "inline"
    # The worker session committed outside this session's identity map.
    sqlite_session.expire_all()
    refreshed_doc = sqlite_session.get(Document, doc.id)
    assert refreshed_doc is not None
    assert refreshed_doc.status == "completed"
    assert refreshed_doc.progress == 100
    assert refreshed_doc.chunk_count > 0
    assert refreshed_doc.character_count > 0

    refreshed_task = sqlite_session.get(Task, task.id)
    assert refreshed_task is not None
    assert refreshed_task.status == "completed"
    assert refreshed_task.celery_task_id is None
    assert (refreshed_task.result or {}).get("chunk_count") == refreshed_doc.chunk_count
