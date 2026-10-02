"""SQLite integrity guarantees for the local (zero-install) preview.

PostgreSQL always enforces ``FOREIGN KEY ... ON DELETE CASCADE``. SQLite only
does when ``PRAGMA foreign_keys = ON`` is set per connection, so a knowledge
base delete used to leave documents, chunks and tasks behind as orphans in the
SQLite preview while behaving correctly in Docker.

These tests pin both halves of the fix: the pragma reaches every connection,
and a knowledge-base delete really removes the child rows and the stored files.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

import app.models  # noqa: F401  — registers every model on Base.metadata
from app.core.config import settings
from app.core.database import enable_sqlite_foreign_keys
from app.models.knowledge import Document, DocumentChunk, KnowledgeBase, Task
from app.models.user import User
from app.services.knowledge_base_service import KnowledgeBaseService


def _seed(db: Session, upload_root: Path) -> tuple[User, int, Document, Path]:
    """Create a user + knowledge base + document + chunk + task on disk."""
    user = User(username="cascade", email="cascade@example.com", hashed_password="x")
    db.add(user)
    db.commit()

    kb = KnowledgeBase(user_id=user.id, name="级联测试库")
    db.add(kb)
    db.commit()

    stored = upload_root / str(user.id) / "stored.txt"
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_text("级联测试内容", encoding="utf-8")

    doc = Document(
        knowledge_base_id=kb.id,
        user_id=user.id,
        filename="stored.txt",
        file_type="txt",
        file_size=stored.stat().st_size,
        storage_path=f"{user.id}/stored.txt",
        status="completed",
        progress=100,
    )
    db.add(doc)
    db.commit()

    db.add(
        DocumentChunk(
            document_id=doc.id,
            knowledge_base_id=kb.id,
            chunk_index=0,
            content="级联测试内容",
        )
    )
    db.add(Task(user_id=user.id, document_id=doc.id, type="document.parse"))
    db.commit()
    return user, kb.id, doc, stored


def test_pragma_foreign_keys_is_on(sqlite_session: Session) -> None:
    assert sqlite_session.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_enable_sqlite_foreign_keys_covers_later_connections(tmp_path: Path) -> None:
    """The pragma is attached to the engine, not to one connection."""
    engine = create_engine(f"sqlite:///{tmp_path / 'late.db'}", future=True)
    enable_sqlite_foreign_keys(engine)
    first = engine.connect()
    second = engine.connect()
    try:
        assert first.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert second.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    finally:
        first.close()
        second.close()
        engine.dispose()


def test_deleting_knowledge_base_cascades_children(
    sqlite_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upload_root = tmp_path / "uploads"
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(upload_root), raising=False)
    user, kb_id, doc, stored = _seed(sqlite_session, upload_root)

    KnowledgeBaseService(sqlite_session).delete(kb_id, user.id)

    assert sqlite_session.get(KnowledgeBase, kb_id) is None
    for model in (Document, DocumentChunk, Task):
        remaining = sqlite_session.execute(
            select(func.count()).select_from(model)
        ).scalar_one()
        assert remaining == 0, f"{model.__tablename__} still has {remaining} orphan rows"

    # The stored upload goes with the row: the file must not leak on disk.
    assert not stored.exists()
    assert doc.id is not None  # sanity: the document really existed
