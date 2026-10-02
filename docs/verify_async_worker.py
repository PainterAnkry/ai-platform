"""Verify that a real Celery worker process consumes and completes a parse task.

The production broker is Redis (``CELERY_BROKER_URL`` defaults to redis db 1),
which the Docker Compose stack provides. This check is transport-agnostic so it
can also run on a machine without Redis or Docker — Kombu's ``sqlalchemy``
broker (a plain SQLite file) is the usual stand-in. What is *not* simulated is
the part that matters: a second OS process, running ``celery worker``, pulling
the task, executing the real parse pipeline and writing the results back.

Terminal 1 — worker (env must match the values printed by this script):

    $env:DATABASE_URL="sqlite:///F:/workspace/ai-platform/backend/ai_platform.db"
    $env:CELERY_BROKER_URL="sqlalchemy+sqlite:///C:/Users/<you>/AppData/Local/Temp/celery-verify/broker.db"
    $env:CELERY_RESULT_BACKEND="db+sqlite:///C:/Users/<you>/AppData/Local/Temp/celery-verify/results.db"
    celery -A app.workers.celery_app:celery_app worker -Q ai_platform --pool=solo --loglevel=info

Terminal 2 — this script (same env):

    python docs/verify_async_worker.py

Exit code 0 means the worker really parsed the document.
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

VERIFY_DIR = Path(
    os.environ.get("CELERY_VERIFY_DIR", str(Path(os.environ.get("TEMP", "/tmp")) / "celery-verify"))
)
VERIFY_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(BACKEND_ROOT / 'ai_platform.db').as_posix()}")
os.environ.setdefault(
    "CELERY_BROKER_URL", f"sqlalchemy+sqlite:///{(VERIFY_DIR / 'broker.db').as_posix()}"
)
os.environ.setdefault(
    "CELERY_RESULT_BACKEND", f"db+sqlite:///{(VERIFY_DIR / 'results.db').as_posix()}"
)

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

import app.models  # noqa: F401,E402  — registers models on Base.metadata
from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.knowledge import Document, DocumentChunk, KnowledgeBase, Task  # noqa: E402
from app.models.user import User  # noqa: E402
from app.schemas.task import TASK_TYPE_DOCUMENT_PARSE  # noqa: E402
from app.workers.tasks import parse_document_task  # noqa: E402

TIMEOUT_SECONDS = float(os.environ.get("VERIFY_TIMEOUT", "90"))
BODY = (
    "第一章 异步解析验证\n\n"
    "这一段文字用于确认 Celery worker 真的执行了解析管线，而不是内联执行。\n\n"
) + ("补充内容：切片数量应当大于一。" * 60)

failures: list[str] = []


def check(label: str, cond: bool, extra: object = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}{(' -> ' + str(extra)) if extra else ''}")
    if not cond:
        failures.append(label)


print("=" * 64)
print("configuration")
print("  database        :", settings.database_url)
print("  broker          :", settings.celery_broker_url)
print("  result backend  :", settings.celery_result_backend)
print("  eager mode      :", settings.CELERY_TASK_ALWAYS_EAGER)
if settings.CELERY_TASK_ALWAYS_EAGER:
    print("  NOTE: CELERY_TASK_ALWAYS_EAGER is set — no worker will be involved.")

# ---------------------------------------------------------------------------
# Seed a pending document + task, exactly like the upload endpoint would
# ---------------------------------------------------------------------------
db = SessionLocal()
try:
    try:
        db.execute(select(func.count()).select_from(User)).scalar_one()
    except OperationalError:
        print("Database schema missing. Run `alembic upgrade head` in backend/ first.")
        raise SystemExit(2)

    suffix = uuid.uuid4().hex[:8]
    user = User(username=f"worker{suffix}", email=f"worker{suffix}@example.com", hashed_password="x")
    db.add(user)
    db.commit()

    kb = KnowledgeBase(user_id=user.id, name="异步解析验证库")
    db.add(kb)
    db.commit()

    stored = VERIFY_DIR / f"sample-{suffix}.txt"
    stored.write_bytes(BODY.encode("utf-8"))

    doc = Document(
        knowledge_base_id=kb.id,
        user_id=user.id,
        filename="sample.txt",
        file_type="txt",
        file_size=stored.stat().st_size,
        storage_path=str(stored),  # absolute: the worker resolves it as-is
        status="pending",
        progress=0,
    )
    db.add(doc)
    db.commit()

    task = Task(
        user_id=user.id,
        document_id=doc.id,
        type=TASK_TYPE_DOCUMENT_PARSE,
        payload={"document_id": doc.id, "storage_path": doc.storage_path, "file_type": "txt"},
    )
    db.add(task)
    db.commit()
    task_id, doc_id, kb_id = task.id, doc.id, kb.id
    print("=" * 64)
    print(f"seeded task {task_id} / document {doc_id} (status pending)")

    # -----------------------------------------------------------------------
    # Publish through the configured broker
    # -----------------------------------------------------------------------
    print("=" * 64)
    print("publishing to the broker")
    result = parse_document_task.delay(task_id)
    celery_id = getattr(result, "id", None)
    check("publish returned a task id", bool(celery_id), celery_id)
    # The API records this when it queues a real upload; mirror it here so the
    # worker's own commits are proven not to clobber the bookkeeping.
    task.celery_task_id = celery_id
    db.commit()

    # -----------------------------------------------------------------------
    # Wait for the worker to finish
    # -----------------------------------------------------------------------
    print("=" * 64)
    print(f"waiting up to {TIMEOUT_SECONDS:.0f}s for a worker to finish the task")
    observed_running = False
    deadline = time.monotonic() + TIMEOUT_SECONDS
    final_status = None
    while time.monotonic() < deadline:
        db.expire_all()
        row = db.get(Task, task_id)
        if row is None:
            break
        if row.status == "running":
            observed_running = True
            print(f"    ... worker started (progress {row.progress}%)")
        if row.status in ("completed", "failed"):
            final_status = row.status
            break
        time.sleep(0.5)

    check("task reached a terminal state", final_status is not None, final_status)
    check("task completed by the worker", final_status == "completed", final_status)
    check("progress checkpoints were visible", observed_running or final_status == "completed")

    db.expire_all()
    task_row = db.get(Task, task_id)
    doc_row = db.get(Document, doc_id)
    assert task_row is not None and doc_row is not None

    check("task progress = 100", task_row.progress == 100, task_row.progress)
    check("task kept its celery id", task_row.celery_task_id == celery_id, task_row.celery_task_id)
    check("task finished_at set", task_row.finished_at is not None, task_row.finished_at)
    check("task error empty", not task_row.error_message, task_row.error_message)

    result_payload = task_row.result or {}
    check("task result has chunk_count", "chunk_count" in result_payload, result_payload)
    check("document completed", doc_row.status == "completed", doc_row.status)
    check("document progress = 100", doc_row.progress == 100, doc_row.progress)
    check("document chunk_count > 0", doc_row.chunk_count > 0, doc_row.chunk_count)
    check("document character_count > 0", doc_row.character_count > 0, doc_row.character_count)
    check("document page_count = 1", doc_row.page_count == 1, doc_row.page_count)
    check(
        "result matches document",
        result_payload.get("chunk_count") == doc_row.chunk_count,
        f"{result_payload.get('chunk_count')} vs {doc_row.chunk_count}",
    )

    chunk_rows = db.execute(
        select(func.count()).select_from(DocumentChunk).where(DocumentChunk.document_id == doc_id)
    ).scalar_one()
    check("chunk rows persisted", chunk_rows == doc_row.chunk_count, f"{chunk_rows} rows")
    first_chunk = db.execute(
        select(DocumentChunk).where(DocumentChunk.document_id == doc_id).order_by(DocumentChunk.chunk_index)
    ).scalars().first()
    check("first chunk has content", bool(first_chunk and first_chunk.content), "")

    # -----------------------------------------------------------------------
    # Clean up (cascade delete, so nothing is left behind)
    # -----------------------------------------------------------------------
    db.delete(db.get(KnowledgeBase, kb_id))
    db.commit()
    stored.unlink(missing_ok=True)
    db.delete(db.get(User, user.id))
    db.commit()
finally:
    db.close()

print("=" * 64)
if failures:
    print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
    raise SystemExit(1)
print("RESULT: worker executed the task end to end")
