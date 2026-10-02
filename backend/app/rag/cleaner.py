"""Text normalisation applied between parsing and chunking.

The project is Chinese-first: normalisation never removes non-ASCII
characters. It only rewrites invisible/structural noise (BOM, zero-width
markers, CRLF endings, long runs of spaces and blank lines).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from app.rag.parsers import PAGE_SEPARATOR, ParsedPage

# BOM, zero-width space/joiner, word joiner, soft hyphen and bidi controls.
_ZERO_WIDTH_RE = re.compile(
    "["
    "\ufeff"  # BOM / zero-width no-break space
    "\u200b"  # zero-width space
    "\u200c"  # zero-width non-joiner
    "\u200d"  # zero-width joiner
    "\u200e\u200f"  # LTR/RTL marks
    "\u202a-\u202e"  # bidi embedding/override
    "\u2060"  # word joiner
    "\u00ad"  # soft hyphen
    "]"
)

# Horizontal whitespace that is safe to squash. NBSP and the ideographic
# space are included because both appear routinely in exported documents.
_SPACE_RUN_RE = re.compile("[ \t\u00a0\u3000]+")

# Three or more newlines == two or more blank lines; keep a single one.
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def clean_text(raw: str) -> str:
    """Normalise ``raw`` text.

    Steps (in order): NFC unicode normalisation, zero-width/BOM removal,
    CRLF/CR -> LF, per-line trailing whitespace strip, runs of horizontal
    whitespace collapsed to a single space, 3+ newlines collapsed to one
    blank line, finally strip the whole string.

    All non-ASCII content (CJK in particular) is preserved verbatim.
    """
    if not raw:
        return ""

    text = unicodedata.normalize("NFC", raw)
    text = _ZERO_WIDTH_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _SPACE_RUN_RE.sub(" ", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


def clean_pages(pages: Iterable[ParsedPage]) -> list[ParsedPage]:
    """Clean every page and recompute monotonic absolute offsets.

    Pages are **kept even when they clean to an empty string** so that
    ``page_number`` stays truthful; skipping empty content is the chunker's
    job.
    """
    cleaned: list[ParsedPage] = []
    cursor = 0
    for index, page in enumerate(pages):
        text = clean_text(page.text)
        if index > 0:
            cursor += len(PAGE_SEPARATOR)
        cleaned.append(
            ParsedPage(
                page_number=page.page_number,
                text=text,
                char_start=cursor,
                char_end=cursor + len(text),
            )
        )
        cursor += len(text)
    return cleaned
