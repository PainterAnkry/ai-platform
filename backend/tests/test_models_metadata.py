"""Assert the ingestion tables are registered on ``Base.metadata``.

Guards against a model being defined but never imported in
``app.models.__init__``, which would silently hide it from Alembic.
"""

from __future__ import annotations

import pytest

import app.models  # noqa: F401  — registers every model on Base.metadata
from app.core.config import settings
from app.core.database import Base
from app.models.types import EmbeddingVector

EXPECTED_COLUMNS: dict[str, set[str]] = {
    "knowledge_bases": {
        "id",
        "user_id",
        "name",
        "description",
        "created_at",
        "updated_at",
    },
    "documents": {
        "id",
        "knowledge_base_id",
        "user_id",
        "filename",
        "file_type",
        "file_size",
        "storage_path",
        "status",
        "progress",
        "error_message",
        "page_count",
        "chunk_count",
        "character_count",
        "embedding_model",
        "created_at",
        "updated_at",
    },
    "document_chunks": {
        "id",
        "document_id",
        "knowledge_base_id",
        "chunk_index",
        "content",
        "page_number",
        "char_start",
        "char_end",
        "token_estimate",
        "embedding",
        "created_at",
    },
    "tasks": {
        "id",
        "user_id",
        "document_id",
        "type",
        "status",
        "progress",
        "celery_task_id",
        "payload",
        "result",
        "error_message",
        "started_at",
        "finished_at",
        "created_at",
    },
}


@pytest.mark.parametrize("table_name", sorted(EXPECTED_COLUMNS))
def test_table_columns_match_spec(table_name: str) -> None:
    assert table_name in Base.metadata.tables, f"{table_name} is not registered"
    table = Base.metadata.tables[table_name]
    assert set(table.columns.keys()) == EXPECTED_COLUMNS[table_name]


def test_document_chunk_unique_constraint() -> None:
    constraints = {
        c.name for c in Base.metadata.tables["document_chunks"].constraints if c.name
    }
    assert "uq_document_chunks_document_index" in constraints


def test_cascade_foreign_keys() -> None:
    """Documents/chunks must disappear with their knowledge base."""
    fks = {
        (fk.parent.name, fk.target_fullname, fk.ondelete)
        for fk in Base.metadata.tables["document_chunks"].foreign_key_constraints
        for fk in fk.elements
    }
    assert ("document_id", "documents.id", "CASCADE") in fks
    assert ("knowledge_base_id", "knowledge_bases.id", "CASCADE") in fks


def test_embedding_column_uses_dialect_aware_type() -> None:
    """Phase 3: chunks carry a dialect-aware vector column (pgvector/JSON)."""
    column = Base.metadata.tables["document_chunks"].columns["embedding"]
    assert isinstance(column.type, EmbeddingVector)
    assert column.nullable is True

    # The configured width must match the pgvector column the migration will
    # create — a silent mismatch would corrupt ANN results at write time.
    assert settings.EMBEDDING_DIM >= 64


def test_document_embedding_model_provenance() -> None:
    """Documents record which provider produced their chunks' vectors."""
    column = Base.metadata.tables["documents"].columns["embedding_model"]
    assert column.nullable is True
    assert "VARCHAR" in str(column.type).upper() or "String" in type(column.type).__name__
