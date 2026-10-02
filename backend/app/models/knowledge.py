"""Knowledge base, document, chunk and task ORM models.

Phase 2 introduces the ingestion domain: a user owns knowledge bases, each
knowledge base holds documents, and each document is split into chunks that
Phase 3 turns into vectors. The ``embedding`` column is dialect-aware:
``vector(EMBEDDING_DIM)`` under PostgreSQL (pgvector) and a JSON list under
the SQLite preview — see :mod:`app.models.types`.

Every row carries ``user_id`` so that repositories can scope reads to the
owner without joining through the knowledge base.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.types import EmbeddingVector


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KnowledgeBase(Base):
    """A named container owned by a single user."""

    __tablename__ = "knowledge_bases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(1024), default="", nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<KnowledgeBase id={self.id} name={self.name!r}>"


class Document(Base):
    """An uploaded file plus the state of its ingestion pipeline.

    ``status`` moves through ``pending -> processing -> completed|failed`` and
    ``progress`` is a coarse 0-100 percentage owned by the worker.
    """

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    knowledge_base_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), index=True, nullable=False
    )

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(32), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)

    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    character_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Provenance of the vectors currently attached to this document's chunks
    # (e.g. "mock:1024" or "openai:text-embedding-3-small"). NULL means the
    # chunks have not been embedded yet; a value that no longer matches the
    # configured provider signals stale vectors worth re-embedding.
    embedding_model: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Document id={self.id} kb_id={self.knowledge_base_id} "
            f"filename={self.filename!r} status={self.status!r}>"
        )


class DocumentChunk(Base):
    """A slice of a document's text, produced by the parsing worker.

    ``knowledge_base_id`` is denormalised so Phase 3 can filter a vector search
    by knowledge base without a join.
    """

    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "chunk_index", name="uq_document_chunks_document_index"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    knowledge_base_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    char_start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    char_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Unit-length vector produced by the embedding provider; NULL until the
    # ingestion worker (or a re-embed task) fills it in. Searches must always
    # filter ``embedding IS NOT NULL``.
    embedding: Mapped[Optional[list[float]]] = mapped_column(
        EmbeddingVector, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<DocumentChunk id={self.id} document_id={self.document_id} idx={self.chunk_index}>"


class Task(Base):
    """A unit of background work (parsing, embedding generation, ...).

    ``celery_task_id`` links the row to the queue job once dispatched, so a
    restart can reconcile rows stuck in ``running``.
    """

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), index=True, nullable=False
    )
    document_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=True,
    )

    type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    celery_task_id: Mapped[Optional[str]] = mapped_column(
        String(128), index=True, nullable=True
    )
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Task id={self.id} type={self.type!r} status={self.status!r}>"
