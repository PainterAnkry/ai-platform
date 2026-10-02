"""Dialect-aware column types.

The project runs PostgreSQL in Docker but keeps a zero-install SQLite preview
with identical behaviour. Vector storage is the one place the dialects genuinely
differ, so it is hidden behind a single :class:`EmbeddingVector` type:

* PostgreSQL → ``pgvector``'s ``vector(dim)`` column (supports ANN indexes).
* SQLite     → ``JSON`` list of floats (brute-force cosine in Python).

Both surface the same Python type: ``list[float] | None``.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import JSON
from sqlalchemy.types import TypeDecorator

from app.core.config import settings


class EmbeddingVector(TypeDecorator):
    """Embedding column: ``vector(EMBEDDING_DIM)`` on PG, ``JSON`` elsewhere."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            # Imported lazily: the pgvector package is only guaranteed to be
            # installed where PostgreSQL is actually used.
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(settings.EMBEDDING_DIM))
        return dialect.type_descriptor(JSON())

    def bind_processor(self, dialect: Any) -> Any:
        """Force SQL NULL for None on every dialect.

        JSON's own bind processor renders Python ``None`` as the JSON *text*
        ``'null'`` (SQLAlchemy treats JSON null and SQL NULL as distinct), so
        an explicitly-cleared embedding would sneak past every
        ``embedding IS NOT NULL`` filter and surface in vector search. The
        None check here short-circuits before the impl processor runs — for
        pgvector as well, whose serializer has the same hazard.
        """
        processor = super().bind_processor(dialect)

        def process(value: Any) -> Any:
            if value is None:
                return None
            return processor(value)

        return process

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        # Plain lists work for both pgvector serialization and JSON. Reject
        # wrong widths here so a provider/dimension mismatch fails loudly at
        # write time instead of poisoning the index silently.
        values = [float(x) for x in value]
        if len(values) != settings.EMBEDDING_DIM:
            raise ValueError(
                f"embedding dim {len(values)} != configured EMBEDDING_DIM "
                f"{settings.EMBEDDING_DIM}"
            )
        return values

    def process_result_value(self, value: Any, dialect: Any) -> Optional[list[float]]:
        if value is None:
            return None
        # pgvector may hand back numpy arrays when numpy is installed.
        return [float(x) for x in value]


__all__ = ["EmbeddingVector"]
