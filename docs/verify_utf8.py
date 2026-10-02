"""Confirm the parser preserves real UTF-8 Chinese (console is GBK, files are not)."""
import os
import sys
import tempfile

sys.path.insert(0, ".")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-please-change-me-1234567890")

from app.rag.parsers import parse_document  # noqa: E402
from app.rag.cleaner import clean_pages  # noqa: E402
from app.rag.chunker import chunk_document  # noqa: E402

out_lines = []
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "cn.txt")
    original = "季度销售报告\n本季度总销售额为 120 万元，同比增长 18%。\n华东区域表现最好。"
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(original)

    parsed = parse_document(p, "txt")
    cleaned = clean_pages(parsed.pages)
    chunks = chunk_document(parsed, chunk_size=200, chunk_overlap=40)

    out_lines.append(f"roundtrip identical : {cleaned[0].text == original}")
    out_lines.append(f"parsed text         : {cleaned[0].text}")
    out_lines.append(f"chunk[0].content    : {chunks[0].content}")
    out_lines.append(f"chinese chars kept  : {''.join(c for c in cleaned[0].text if '\u4e00' <= c <= '\u9fff')[:20]}")

report = "\n".join(out_lines)
with open("_verify_utf8.txt", "w", encoding="utf-8") as fh:
    fh.write(report)
print("written to _verify_utf8.txt")
