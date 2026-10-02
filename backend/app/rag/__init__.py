"""Retrieval-augmented-generation helpers for Phase 2 document ingestion.

Three pure, dependency-light steps that turn an uploaded file into chunks:

1. :mod:`app.rag.parsers` — file bytes -> ``ParsedDocument`` (pages + offsets)
2. :mod:`app.rag.cleaner` — Unicode/whitespace normalisation
3. :mod:`app.rag.chunker` — ``ParsedDocument`` -> ``list[Chunk]``

None of these modules touch the database or the queue, so they can be unit
tested and reused from the API layer or a Celery worker alike.
"""

from app.rag.cleaner import clean_pages, clean_text
from app.rag.chunker import Chunk, chunk_document, estimate_tokens
from app.rag.parsers import (
    ParsedDocument,
    ParsedPage,
    detect_file_type,
    parse_document,
    parse_docx,
    parse_pdf,
    parse_txt,
)

__all__ = [
    "Chunk",
    "ParsedDocument",
    "ParsedPage",
    "chunk_document",
    "clean_pages",
    "clean_text",
    "detect_file_type",
    "estimate_tokens",
    "parse_document",
    "parse_docx",
    "parse_pdf",
    "parse_txt",
]
