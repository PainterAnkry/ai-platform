"""Split a :class:`~app.rag.parsers.ParsedDocument` into retrievable chunks.

Chunking never crosses a page boundary, so ``page_number`` on every chunk is
always truthful. ``char_start``/``char_end`` are absolute document offsets
(page offset + offset inside the page) and ``chunk_index`` is a dense 0-based
sequence — ``document_chunks`` has a unique constraint on
``(document_id, chunk_index)``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.rag.parsers import ParsedDocument

# CJK ideographs, kana and hangul — roughly one token per character.
_CJK_RE = re.compile("[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")

# Latin-ish characters are roughly 4 characters per token.
_CHARS_PER_TOKEN = 4

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 120


@dataclass
class Chunk:
    """One retrievable slice of a document."""

    chunk_index: int
    content: str
    page_number: int | None
    char_start: int
    char_end: int
    token_estimate: int

    @property
    def character_count(self) -> int:
        return len(self.content)


def estimate_tokens(text: str) -> int:
    """Rough token count: 1 per CJK character, 1 per 4 other characters."""
    if not text:
        return 0
    cjk_count = len(_CJK_RE.findall(text))
    other_count = len(text) - cjk_count
    return cjk_count + other_count // _CHARS_PER_TOKEN


def chunk_document(
    parsed: ParsedDocument,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[Chunk]:
    """Split ``parsed`` into overlapping chunks, one page at a time.

    Args:
        parsed: cleaned :class:`ParsedDocument` (run it through
            :func:`app.rag.cleaner.clean_pages` first for best results).
        chunk_size: maximum characters per chunk; must be > 0.
        chunk_overlap: characters repeated between two consecutive chunks of
            the same page; must satisfy ``0 <= chunk_overlap < chunk_size``.

    Returns:
        Chunks with dense 0-based ``chunk_index`` values. Empty or
        whitespace-only pages are skipped.

    Raises:
        ValueError: invalid ``chunk_size``/``chunk_overlap`` combination.
    """
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be > 0, got {chunk_size}")
    if chunk_overlap < 0:
        raise ValueError(f"chunk_overlap must be >= 0, got {chunk_overlap}")
    if chunk_overlap >= chunk_size:
        raise ValueError(
            f"chunk_overlap ({chunk_overlap}) must be smaller than "
            f"chunk_size ({chunk_size})"
        )

    stride = chunk_size - chunk_overlap
    chunks: list[Chunk] = []

    for page in parsed.pages:
        text = page.text
        if not text.strip():
            continue

        if len(text) <= chunk_size:
            _append_chunk(
                chunks,
                content=text,
                page_number=page.page_number,
                char_start=page.char_start,
                token_estimate=estimate_tokens(text),
            )
            continue

        start = 0
        length = len(text)
        while start < length:
            window = text[start : start + chunk_size]
            stripped = window.strip()
            if stripped:
                leading = len(window) - len(window.lstrip())
                _append_chunk(
                    chunks,
                    content=stripped,
                    page_number=page.page_number,
                    char_start=page.char_start + start + leading,
                    token_estimate=estimate_tokens(stripped),
                )
            if start + chunk_size >= length:
                break
            start += stride

    return chunks


def _append_chunk(
    chunks: list[Chunk],
    *,
    content: str,
    page_number: int | None,
    char_start: int,
    token_estimate: int,
) -> None:
    """Append a chunk with a dense index and end offset derived from content."""
    chunks.append(
        Chunk(
            chunk_index=len(chunks),
            content=content,
            page_number=page_number,
            char_start=char_start,
            char_end=char_start + len(content),
            token_estimate=token_estimate,
        )
    )
