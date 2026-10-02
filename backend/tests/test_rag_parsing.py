"""Tests for the Phase 2 parsers and text cleaner.

All fixtures are generated programmatically in ``tmp_path`` — no binary files
are committed. The PDF fixture is a minimal, hand-assembled PDF (correct
xref table + Helvetica base font) so the real ``pypdf`` code path is
exercised end to end.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from app.rag.cleaner import clean_pages, clean_text
from app.rag.parsers import (
    PAGE_SEPARATOR,
    ParsedPage,
    detect_file_type,
    parse_document,
    parse_docx,
    parse_pdf,
    parse_txt,
)

CHINESE_TEXT = "人工智能知识库\n第二行：文档解析与向量化。\nEnglish mixed line."


# ---------------------------------------------------------------------------
# Minimal PDF builder (no external writer available offline)
# ---------------------------------------------------------------------------
def _build_pdf(page_texts: list[str]) -> bytes:
    """Assemble a valid single- or multi-page PDF from plain strings."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)  # object numbers are 1-based

    catalog_num = add(b"")
    pages_num = add(b"")
    font_num = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    page_nums: list[int] = []
    for text in page_texts:
        if text:
            escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            content = f"BT /F1 18 Tf 72 700 Td ({escaped}) Tj ET".encode("latin-1")
        else:
            content = b""
        stream = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content)
        content_num = add(stream)
        page_nums.append(
            add(
                (
                    "<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
                    "/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
                    % (pages_num, font_num, content_num)
                ).encode("latin-1")
            )
        )

    kids = b" ".join(b"%d 0 R" % num for num in page_nums)
    objects[catalog_num - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_num
    objects[pages_num - 1] = (
        b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % len(page_texts)
    )

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % index + body + b"\nendobj\n"

    xref_pos = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        catalog_num,
        xref_pos,
    )
    return bytes(out)


def _write_pdf(path: Path, page_texts: list[str]) -> Path:
    path.write_bytes(_build_pdf(page_texts))
    return path


def _write_encrypted_pdf(path: Path, page_texts: list[str]) -> Path:
    from pypdf import PdfWriter

    writer = PdfWriter(clone_from=io.BytesIO(_build_pdf(page_texts)))
    writer.encrypt("secret")
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def _make_docx(path: Path, paragraphs: list[str]) -> Path:
    import docx

    document = docx.Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    document.save(str(path))
    return path


# ---------------------------------------------------------------------------
# detect_file_type / dispatch
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("report.pdf", "pdf"),
        ("Report.PDF", "pdf"),
        ("notes.TXT", "txt"),
        ("/tmp/a/b/手册.docx", "docx"),
    ],
)
def test_detect_file_type(filename: str, expected: str) -> None:
    assert detect_file_type(filename) == expected


@pytest.mark.parametrize("filename", ["archive.zip", "noextension", "x.doc", ".pdf.bak"])
def test_detect_file_type_rejects_unsupported(filename: str) -> None:
    with pytest.raises(ValueError):
        detect_file_type(filename)


def test_parse_document_dispatches_on_file_type(tmp_path: Path) -> None:
    txt = tmp_path / "a.txt"
    txt.write_text("hello", encoding="utf-8")
    assert parse_document(txt, "txt").file_type == "txt"
    assert parse_document(str(txt), ".TXT").file_type == "txt"


def test_parse_document_rejects_unsupported_type(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_document(f, "zip")


# ---------------------------------------------------------------------------
# TXT
# ---------------------------------------------------------------------------
def test_parse_txt_chinese_utf8(tmp_path: Path) -> None:
    path = tmp_path / "中文.txt"
    # write_bytes (not write_text) so Windows newline translation cannot
    # rewrite the LF characters into CRLF behind the parser's back.
    path.write_bytes(CHINESE_TEXT.encode("utf-8"))

    parsed = parse_txt(path)

    assert parsed.file_type == "txt"
    assert parsed.page_count == 1
    assert parsed.pages[0].page_number == 1
    assert parsed.pages[0].text == CHINESE_TEXT
    assert parsed.character_count == len(CHINESE_TEXT)
    assert parsed.pages[0].char_start == 0
    assert parsed.pages[0].char_end == len(CHINESE_TEXT)


def test_parse_txt_falls_back_to_gbk(tmp_path: Path) -> None:
    path = tmp_path / "gbk.txt"
    path.write_bytes(CHINESE_TEXT.encode("gbk"))

    parsed = parse_txt(path)

    assert parsed.pages[0].text == CHINESE_TEXT


def test_parse_txt_strips_utf8_bom(tmp_path: Path) -> None:
    path = tmp_path / "bom.txt"
    path.write_bytes(b"\xef\xbb\xbf" + "带 BOM 的文本".encode("utf-8"))

    parsed = parse_txt(path)

    assert parsed.pages[0].text == "带 BOM 的文本"


def test_parse_txt_empty_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "empty.txt"
    path.write_text("   \n\n", encoding="utf-8")

    with pytest.raises(ValueError, match="empty"):
        parse_txt(path)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        parse_txt(tmp_path / "nope.txt")
    with pytest.raises(ValueError, match="not found"):
        parse_pdf(tmp_path / "nope.pdf")
    with pytest.raises(ValueError, match="not found"):
        parse_docx(tmp_path / "nope.docx")


def test_invalid_docx_raises_value_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.docx"
    path.write_bytes(b"not a zip file at all")

    with pytest.raises(ValueError):
        parse_docx(path)


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------
def test_parse_docx_single_page(tmp_path: Path) -> None:
    path = _make_docx(tmp_path / "a.docx", ["第一段内容", "", "Second paragraph", "   "])

    parsed = parse_docx(path)

    assert parsed.file_type == "docx"
    assert parsed.page_count == 1
    assert parsed.pages[0].page_number == 1
    assert parsed.pages[0].text == "第一段内容\nSecond paragraph"
    assert parsed.character_count == len(parsed.pages[0].text)


def test_parse_docx_without_text_raises(tmp_path: Path) -> None:
    path = _make_docx(tmp_path / "empty.docx", ["", "  "])

    with pytest.raises(ValueError, match="no text"):
        parse_docx(path)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def test_parse_pdf_keeps_empty_pages_aligned(tmp_path: Path) -> None:
    path = _write_pdf(tmp_path / "a.pdf", ["Page one text", "", "Page three text"])

    parsed = parse_pdf(path)

    assert parsed.file_type == "pdf"
    assert parsed.page_count == 3
    assert [p.page_number for p in parsed.pages] == [1, 2, 3]
    assert parsed.pages[1].text == ""
    assert parsed.pages[0].text == "Page one text"
    assert parsed.pages[2].text == "Page three text"
    assert parsed.character_count == len("Page one text") + len("Page three text")
    # Offsets stay monotonic and are separated by PAGE_SEPARATOR.
    assert parsed.pages[1].char_start == len("Page one text") + len(PAGE_SEPARATOR)
    assert parsed.pages[1].char_end == parsed.pages[1].char_start  # empty page
    assert parsed.pages[2].char_start == (
        parsed.pages[1].char_start + len(PAGE_SEPARATOR)
    )
    assert parsed.full_text() == f"Page one text{PAGE_SEPARATOR}{PAGE_SEPARATOR}Page three text"


def test_parse_pdf_without_any_text_raises(tmp_path: Path) -> None:
    path = _write_pdf(tmp_path / "blank.pdf", ["", ""])

    with pytest.raises(ValueError, match="no extractable text"):
        parse_pdf(path)


def test_parse_pdf_encrypted_raises(tmp_path: Path) -> None:
    path = _write_encrypted_pdf(tmp_path / "locked.pdf", ["secret text"])

    with pytest.raises(ValueError, match="encrypted"):
        parse_pdf(path)


def test_parse_pdf_zero_pages_raises(tmp_path: Path) -> None:
    path = _write_pdf(tmp_path / "nopages.pdf", [])

    with pytest.raises(ValueError, match="no pages"):
        parse_pdf(path)


# ---------------------------------------------------------------------------
# Cleaner
# ---------------------------------------------------------------------------
def test_clean_text_normalises_but_keeps_chinese() -> None:
    raw = "\ufeff中文\u200b第一行\ufeff\r\n\r\n\r\n\r\n第二行   \t  空格  \r\n"

    cleaned = clean_text(raw)

    assert cleaned == "中文第一行\n\n第二行 空格"
    assert "中" in cleaned and "第一行" in cleaned


def test_clean_text_collapses_blank_lines_and_spaces() -> None:
    assert clean_text("a\n\n\n\n\nb") == "a\n\nb"
    assert clean_text("a  \t  b") == "a b"
    assert clean_text("a　　b") == "a b"  # ideographic space
    assert clean_text("") == ""
    assert clean_text("\n\n  \n") == ""


def test_clean_text_normalises_unicode_to_nfc() -> None:
    decomposed = "é"  # E + COMBINING ACUTE ACCENT
    assert clean_text(decomposed) == "é"
    assert clean_text(decomposed) == "é"


def test_clean_pages_recomputes_offsets_and_keeps_empty_pages() -> None:
    pages = [
        ParsedPage(page_number=1, text="  第一段  ", char_start=0, char_end=6),
        ParsedPage(page_number=2, text="\n\n\n", char_start=8, char_end=11),
        ParsedPage(page_number=3, text="第三段", char_start=13, char_end=16),
    ]

    cleaned = clean_pages(pages)

    assert [p.page_number for p in cleaned] == [1, 2, 3]
    assert [p.text for p in cleaned] == ["第一段", "", "第三段"]
    assert cleaned[0].char_start == 0
    assert cleaned[0].char_end == len("第一段")
    assert cleaned[1].char_start == cleaned[0].char_end + len(PAGE_SEPARATOR)
    assert cleaned[2].char_start == cleaned[1].char_start + len(PAGE_SEPARATOR)
    for page in cleaned:
        assert page.char_end - page.char_start == len(page.text)
    # Previous list must not be mutated.
    assert pages[0].text == "  第一段  "
