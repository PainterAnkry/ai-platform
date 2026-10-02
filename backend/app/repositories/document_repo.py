"""Document repository — pure DB access, no business logic."""

from __future__ import annotations

import math
from typing import Any, Optional, Sequence

from sqlalchemy import delete as sql_delete
from sqlalchemy import Float, func, select
from sqlalchemy.orm import Session

from app.models.knowledge import Document, DocumentChunk


class VectorHit:
    """One row of a vector search result (scored, no ORM object attached).

    Plain dataclass instead of the ORM entity so the SQLite fallback (which
    scores rows in Python) and the pgvector path return the same shape.
    """

    __slots__ = ("chunk_id", "document_id", "content", "page_number",
                 "chunk_index", "score")

    def __init__(
        self,
        *,
        chunk_id: int,
        document_id: int,
        content: str,
        page_number: Optional[int],
        chunk_index: int,
        score: float,
    ) -> None:
        self.chunk_id = chunk_id
        self.document_id = document_id
        self.content = content
        self.page_number = page_number
        self.chunk_index = chunk_index
        self.score = score

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<VectorHit chunk={self.chunk_id} doc={self.document_id} "
            f"page={self.page_number} score={self.score:.4f}>"
        )


class DocumentRepository:
    """Encapsulates all SQL access for the ``documents`` table.

    Chunk writes go through :meth:`replace_chunks`, which leaves the
    transaction open so the caller (the ingestion service) commits once the
    document counters are updated in the same transaction.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ---------- lookups ----------
    def get_by_id(self, document_id: int) -> Optional[Document]:
        return self.db.get(Document, document_id)

    def list_by_kb(
        self, kb_id: int, skip: int = 0, limit: int = 100
    ) -> list[Document]:
        stmt = (
            select(Document)
            .where(Document.knowledge_base_id == kb_id)
            .order_by(Document.id.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())

    def count_by_kb(self, kb_id: int) -> int:
        stmt = select(func.count()).select_from(Document).where(
            Document.knowledge_base_id == kb_id
        )
        return int(self.db.execute(stmt).scalar_one())

    def count_by_user(self, user_id: int) -> int:
        stmt = select(func.count()).select_from(Document).where(
            Document.user_id == user_id
        )
        return int(self.db.execute(stmt).scalar_one())

    def list_by_user(self, user_id: int, limit: int = 100) -> list[Document]:
        stmt = (
            select(Document)
            .where(Document.user_id == user_id)
            .order_by(Document.id.desc())
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())

    # ---------- mutations ----------
    def create(
        self,
        *,
        knowledge_base_id: int,
        user_id: int,
        filename: str,
        file_type: str,
        file_size: int,
        storage_path: str,
        status: str = "pending",
    ) -> Document:
        doc = Document(
            knowledge_base_id=knowledge_base_id,
            user_id=user_id,
            filename=filename,
            file_type=file_type,
            file_size=file_size,
            storage_path=storage_path,
            status=status,
        )
        self.db.add(doc)
        self.db.commit()
        self.db.refresh(doc)
        return doc

    def update(self, doc: Document, **fields: Any) -> Document:
        """Apply a partial update. ``None`` values are ignored."""
        for key, value in fields.items():
            if value is not None:
                setattr(doc, key, value)
        return doc

    def delete(self, doc: Document) -> None:
        self.db.delete(doc)

    # ---------- chunks ----------
    def replace_chunks(self, document_id: int, chunks: list[DocumentChunk]) -> None:
        """Swap in a fresh chunk set for a document.

        Existing chunks are removed in a single statement and the new rows are
        staged in the session. No commit happens here — the caller decides the
        transaction boundary.
        """
        self.db.execute(
            sql_delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )
        if chunks:
            self.db.add_all(chunks)

    def list_chunks_by_document(self, document_id: int) -> list[DocumentChunk]:
        stmt = (
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index)
        )
        return list(self.db.execute(stmt).scalars().all())

    def count_embedded_by_kb(self, kb_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(DocumentChunk)
            .where(
                DocumentChunk.knowledge_base_id == kb_id,
                DocumentChunk.embedding.is_not(None),
            )
        )
        return int(self.db.execute(stmt).scalar_one())

    # ---------- Phase 3: vector search ----------
    def search_vectors(
        self,
        kb_id: int,
        query_embedding: Sequence[float],
        top_k: int,
    ) -> list[VectorHit]:
        """Top-K chunks of a knowledge base by cosine similarity.

        PostgreSQL uses the pgvector ``<=>`` (cosine distance) operator backed
        by the HNSW index; SQLite falls back to loading the KB's vectors and
        scoring them in Python. Both return :class:`VectorHit` in
        best-first order with ``score`` as cosine *similarity* in [-1, 1].
        """
        # Decide on the *actual connection's* dialect, not a global setting:
        # tests (and the SQLite preview) point a SQLAlchemy engine at SQLite
        # while the process-wide config may still name PostgreSQL.
        if self.db.get_bind().dialect.name == "postgresql":
            return self._search_pgvector(kb_id, query_embedding, top_k)
        return self._search_python(kb_id, query_embedding, top_k)

    def _search_pgvector(
        self,
        kb_id: int,
        query_embedding: Sequence[float],
        top_k: int,
    ) -> list[VectorHit]:
        # return_type=Float matters: without it the distance expression
        # inherits the vector column type and pgvector's result processor
        # would try to parse the scalar distance as a vector literal.
        distance = DocumentChunk.embedding.op("<=>", return_type=Float)(
            list(query_embedding)
        )
        stmt = (
            select(
                DocumentChunk.id,
                DocumentChunk.document_id,
                DocumentChunk.content,
                DocumentChunk.page_number,
                DocumentChunk.chunk_index,
                distance.label("distance"),
            )
            .where(
                DocumentChunk.knowledge_base_id == kb_id,
                DocumentChunk.embedding.is_not(None),
            )
            .order_by(distance)
            .limit(top_k)
        )
        rows = self.db.execute(stmt).all()
        return [
            VectorHit(
                chunk_id=row.id,
                document_id=row.document_id,
                content=row.content,
                page_number=row.page_number,
                chunk_index=row.chunk_index,
                score=1.0 - float(row.distance),  # distance -> similarity
            )
            for row in rows
        ]

    def _search_python(
        self,
        kb_id: int,
        query_embedding: Sequence[float],
        top_k: int,
    ) -> list[VectorHit]:
        """Brute-force cosine search for the SQLite preview.

        Documents in a demo knowledge base number in the hundreds of chunks,
        so a Python scan is fast enough and keeps the zero-install path
        dependency-free (no numpy).
        """
        stmt = select(
            DocumentChunk.id,
            DocumentChunk.document_id,
            DocumentChunk.content,
            DocumentChunk.page_number,
            DocumentChunk.chunk_index,
            DocumentChunk.embedding,
        ).where(
            DocumentChunk.knowledge_base_id == kb_id,
            DocumentChunk.embedding.is_not(None),
        )
        hits: list[VectorHit] = []
        for row in self.db.execute(stmt).all():
            score = _cosine_similarity(query_embedding, row.embedding or [])
            hits.append(
                VectorHit(
                    chunk_id=row.id,
                    document_id=row.document_id,
                    content=row.content,
                    page_number=row.page_number,
                    chunk_index=row.chunk_index,
                    score=score,
                )
            )
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:top_k]


def _cosine_similarity(
    a: Sequence[float], b: Sequence[float]
) -> float:
    """Cosine similarity in pure Python (SQLite fallback path only)."""
    if not a or not b:
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))
