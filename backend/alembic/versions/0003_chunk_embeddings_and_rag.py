"""chunk embeddings (pgvector) + document embedding provenance

Revision ID: 0003_chunk_embeddings_and_rag
Revises: 0002_knowledge_base_and_tasks
Create Date: 2026-09-29 00:00:00

PostgreSQL gets a real ``vector(EMBEDDING_DIM)`` column backed by an HNSW
cosine-distance index (the pgvector extension must be available — the compose
file uses the ``pgvector/pgvector`` image for this). The SQLite preview gets a
JSON column instead and falls back to brute-force cosine scoring in Python;
both dialects expose the same ``list[float]`` values in the ORM.

The vector width is frozen at migration time from ``EMBEDDING_DIM``: pgvector
columns have a fixed dimension, so changing it later requires a new migration
(the embedding type validates widths at write time to catch mismatches early).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision: str = "0003_chunk_embeddings_and_rag"
down_revision: Union[str, None] = "0002_knowledge_base_and_tasks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

HNSW_INDEX_NAME = "ix_document_chunks_embedding_hnsw"


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    # Provenance: which provider/model produced the vectors on a document.
    op.add_column(
        "documents",
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
    )

    if _is_postgresql():
        # pgvector ships with the pgvector/pgvector compose image; the
        # extension only needs to be created once per database.
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        from pgvector.sqlalchemy import Vector

        op.add_column(
            "document_chunks",
            sa.Column("embedding", Vector(settings.EMBEDDING_DIM), nullable=True),
        )
        # HNSW over cosine distance. It is built on the whole table; KB-scoped
        # queries filter by knowledge_base_id after the ANN scan, which is the
        # documented pattern for pgvector at this scale.
        op.create_index(
            HNSW_INDEX_NAME,
            "document_chunks",
            ["embedding"],
            unique=False,
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        )
    else:
        # SQLite preview: same ORM values, brute-force search in Python.
        op.add_column(
            "document_chunks",
            sa.Column("embedding", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    if _is_postgresql():
        op.drop_index(HNSW_INDEX_NAME, table_name="document_chunks")
        op.drop_column("document_chunks", "embedding")
    else:
        op.drop_column("document_chunks", "embedding")

    op.drop_column("documents", "embedding_model")
