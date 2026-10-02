"""Tests for the Phase 2 chunker."""

from __future__ import annotations

import pytest

from app.rag.chunker import Chunk, chunk_document, estimate_tokens
from app.rag.cleaner import clean_pages
from app.rag.parsers import PAGE_SEPARATOR, ParsedDocument, ParsedPage


def _document(page_texts: list[str]) -> ParsedDocument:
    """Build a ParsedDocument the same way the parsers do (absolute offsets)."""
    pages: list[ParsedPage] = []
    cursor = 0
    for index, text in enumerate(page_texts, start=1):
        if index > 1:
            cursor += len(PAGE_SEPARATOR)
        pages.append(
            ParsedPage(
                page_number=index,
                text=text,
                char_start=cursor,
                char_end=cursor + len(text),
            )
        )
        cursor += len(text)
    return ParsedDocument(
        pages=pages,
        page_count=len(pages),
        character_count=sum(len(page.text) for page in pages),
        file_type="txt",
    )


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------
def test_estimate_tokens_cjk_is_one_per_char() -> None:
    assert estimate_tokens("中文测试") == 4
    assert estimate_tokens("") == 0


def test_estimate_tokens_latin_is_four_chars_per_token() -> None:
    assert estimate_tokens("a" * 400) == 100
    assert estimate_tokens("a" * 8) == 2


def test_estimate_tokens_mixed_script() -> None:
    # 2 CJK chars (=2 tokens) + 8 latin chars (=2 tokens)
    assert estimate_tokens("中文abcdefgh") == 4


# ---------------------------------------------------------------------------
# argument validation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("chunk_size", "chunk_overlap"),
    [(0, 0), (-1, 0), (100, -1), (100, 100), (100, 200)],
)
def test_chunk_document_validates_arguments(chunk_size: int, chunk_overlap: int) -> None:
    with pytest.raises(ValueError):
        chunk_document(_document(["abc"]), chunk_size=chunk_size, chunk_overlap=chunk_overlap)


# ---------------------------------------------------------------------------
# happy paths
# ---------------------------------------------------------------------------
def test_short_page_becomes_single_chunk() -> None:
    chunks = chunk_document(_document(["短文本"]), chunk_size=100, chunk_overlap=10)

    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].content == "短文本"
    assert chunks[0].page_number == 1
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == 3
    assert chunks[0].token_estimate == 3


def test_long_page_slides_window_with_overlap() -> None:
    text = "".join(str(i % 10) for i in range(100))

    chunks = chunk_document(_document([text]), chunk_size=30, chunk_overlap=10)

    assert len(chunks) == 5  # stride 20: 0-29, 20-49, 40-69, 60-89, 80-99
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert [c.content for c in chunks] == [
        text[0:30],
        text[20:50],
        text[40:70],
        text[60:90],
        text[80:100],
    ]
    # Last chunk reaches the end of the page text.
    assert chunks[-1].char_end == 100
    assert chunks[-1].char_start == 80


def test_chunks_never_cross_pages_and_keep_page_numbers() -> None:
    parsed = _document(["第一页" * 40, "第二页" * 40])

    chunks = chunk_document(parsed, chunk_size=50, chunk_overlap=10)

    assert chunks
    for chunk in chunks:
        assert chunk.page_number in {1, 2}
        assert "第一页" in chunk.content or "第二页" in chunk.content
        assert not ("第一页" in chunk.content and "第二页" in chunk.content)
    assert [c.page_number for c in chunks] == sorted(c.page_number for c in chunks)


def test_char_offsets_are_absolute_document_offsets() -> None:
    page1 = "A" * 30
    page2 = "B" * 30
    parsed = _document([page1, page2])

    chunks = chunk_document(parsed, chunk_size=100, chunk_overlap=10)
    page2_chunks = [c for c in chunks if c.page_number == 2]

    assert page2_chunks
    for chunk in page2_chunks:
        assert chunk.char_start == parsed.pages[1].char_start
        assert chunk.char_start > parsed.pages[0].char_end
        assert chunk.char_end - chunk.char_start == len(chunk.content)


def test_empty_pages_are_skipped_and_indices_stay_dense() -> None:
    parsed = _document(["有内容", "   \n\n  ", "也有内容"])

    chunks = chunk_document(parsed, chunk_size=100, chunk_overlap=10)

    assert [c.chunk_index for c in chunks] == [0, 1]
    assert [c.page_number for c in chunks] == [1, 3]
    assert all(isinstance(c, Chunk) for c in chunks)


def test_whitespace_only_document_produces_no_chunks() -> None:
    assert chunk_document(_document(["", "  \n\t "]), chunk_size=100, chunk_overlap=10) == []


def test_single_character_page_is_emitted() -> None:
    chunks = chunk_document(_document(["字"]), chunk_size=800, chunk_overlap=120)
    assert len(chunks) == 1
    assert chunks[0].content == "字"


def test_pipeline_parse_clean_chunk_keeps_offsets_consistent() -> None:
    raw = "\ufeff第一段内容，含有中文与 English。\r\n\r\n\r\n\r\n第二段内容继续。\r\n   \r\n"
    parsed = _document([raw])
    cleaned_pages = clean_pages(parsed.pages)

    cleaned = ParsedDocument(
        pages=cleaned_pages,
        page_count=len(cleaned_pages),
        character_count=sum(len(p.text) for p in cleaned_pages),
        file_type="txt",
    )
    chunks = chunk_document(cleaned, chunk_size=20, chunk_overlap=5)

    assert chunks
    full_text = cleaned.full_text()
    for chunk in chunks:
        assert chunk.content == full_text[chunk.char_start : chunk.char_end]
        assert chunk.token_estimate == estimate_tokens(chunk.content)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
