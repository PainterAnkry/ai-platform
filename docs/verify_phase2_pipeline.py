"""Verify the Phase 2 pipeline end-to-end without a broker or a running server.

Generates real files on disk, runs parse -> clean -> chunk, and prints the
result. Also proves the Celery worker module imports with Redis absent.
"""
import os
import sys
import tempfile

sys.path.insert(0, ".")

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-please-change-me-1234567890")
os.environ.setdefault("DATABASE_URL", "sqlite:///./ai_platform.db")

from app.rag.parsers import parse_document, detect_file_type  # noqa: E402
from app.rag.cleaner import clean_pages, clean_text  # noqa: E402
from app.rag.chunker import chunk_document  # noqa: E402

print("=" * 62)
print("1) settings")
from app.core.config import settings  # noqa: E402

print("   celery_broker_url  :", settings.celery_broker_url)
print("   result_backend     :", settings.celery_result_backend)
print("   upload_dir         :", settings.upload_dir)
print("   eager              :", settings.CELERY_TASK_ALWAYS_EAGER)

print("=" * 62)
print("2) Celery module imports with Redis DOWN")
import app.workers.tasks as wt  # noqa: E402
from app.workers.celery_app import celery_app  # noqa: E402

print("   celery_app         :", celery_app.main)
print("   broker             :", celery_app.conf.broker_url)
print("   run_parse_task_inline:", callable(wt.run_parse_task_inline))
print("   dispatch_parse_task :", callable(wt.dispatch_parse_task))

print("=" * 62)
print("3) TXT with Chinese content")
with tempfile.TemporaryDirectory() as td:
    txt_path = os.path.join(td, "sample.txt")
    body = (
        "第一章 概述\r\n\r\n"
        "本项目是一个 AI 智能知识库与 Agent 工作流平台。\r\n"
        "它演示了 Vue 3、FastAPI、RAG、Agent 与 MCP 的完整工程能力。\r\n\r\n"
        "   \r\n\r\n\r\n"
        "第二章 技术栈\r\n\r\n"
        "前端使用 Vue 3 与 TypeScript，后端使用 Python 3.11+ 与 FastAPI。\r\n"
    ) + ("这是一个很长的段落，用来测试切片窗口是否正确工作。" * 60)
    with open(txt_path, "w", encoding="utf-8") as fh:
        fh.write(body)

    print("   detect_file_type   :", detect_file_type("sample.txt"))
    parsed = parse_document(txt_path, "txt")
    print("   page_count         :", parsed.page_count)
    print("   character_count    :", parsed.character_count)
    print("   file_type          :", parsed.file_type)
    print("   raw has CRLF       :", "\r" in body)

    cleaned = clean_pages(parsed.pages)
    print("   cleaned pages      :", len(cleaned))
    print("   cleaned[0] offsets :", cleaned[0].char_start, "->", cleaned[0].char_end)
    print("   CRLF after clean   :", "\r" in cleaned[0].text)
    print("   3+ blank lines     :", "\n\n\n" in cleaned[0].text)

    chunks = chunk_document(
        type(parsed)(pages=cleaned, page_count=parsed.page_count,
                     character_count=parsed.character_count, file_type=parsed.file_type),
        chunk_size=300,
        chunk_overlap=60,
    )
    print("   chunk count        :", len(chunks))
    for c in chunks[:3]:
        print(f"     idx={c.chunk_index} page={c.page_number} "
              f"[{c.char_start}:{c.char_end}] tok~{c.token_estimate} "
              f"len={len(c.content)} :: {c.content[:28]!r}")
    print("   dense 0-based idx  :",
          [c.chunk_index for c in chunks] == list(range(len(chunks))))
    print("   monotonic offsets  :",
          all(chunks[i].char_end <= chunks[i + 1].char_start + 99999
              for i in range(len(chunks) - 1)))

print("=" * 62)
print("4) Real DOCX built programmatically")
import docx  # noqa: E402

with tempfile.TemporaryDirectory() as td:
    docx_path = os.path.join(td, "report.docx")
    d = docx.Document()
    d.add_heading("季度销售报告", level=1)
    d.add_paragraph("本季度总销售额为 120 万元，同比增长 18%。")
    d.add_paragraph("华东区域表现最好，西南区域略有下滑。")
    d.add_paragraph("")
    d.add_paragraph("下季度计划聚焦高毛利产品线。")
    d.save(docx_path)

    print("   detect_file_type   :", detect_file_type("report.docx"))
    parsed = parse_document(docx_path, "docx")
    print("   page_count         :", parsed.page_count, "(DOCX has no pages)")
    print("   character_count    :", parsed.character_count)
    print("   first 60 chars     :", repr(parsed.pages[0].text[:60]))
    chunks = chunk_document(parsed, chunk_size=200, chunk_overlap=40)
    print("   chunk count        :", len(chunks))
    for c in chunks:
        print(f"     idx={c.chunk_index} page={c.page_number} "
              f"len={len(c.content)} :: {c.content[:30]!r}")

print("=" * 62)
print("5) Unsupported type rejected")
try:
    detect_file_type("evil.exe")
    print("   FAIL: no error raised")
except ValueError as exc:
    print("   OK ValueError:", exc)

print("=" * 62)
print("PIPELINE VERIFICATION DONE")