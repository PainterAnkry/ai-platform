"""Celery tasks for document ingestion (Phase 2 parse + Phase 3 embedding).

Each pipeline lives in exactly one ``run_*_inline`` function; the Celery task
is a thin wrapper that adds retry bookkeeping, so the same code path runs:

* inside a ``celery worker`` process (normal deployment), or
* inside the API process when ``CELERY_TASK_ALWAYS_EAGER`` is set or when the
  broker cannot be reached (local demo without Redis).

Two pipelines exist:

* :func:`run_parse_task_inline` — parse → clean → chunk → persist → **embed**
  (the framework's RAG ingestion flow; a document is only searchable once its
  chunks carry vectors).
* :func:`run_embed_task_inline` — (re-)embed an already-parsed document, used
  when the embedding provider/model changed or an embedding run failed.

Importing this module never connects to Redis.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from celery import Task as CeleryTask

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.knowledge import Document, DocumentChunk, Task
from app.rag.cleaner import clean_pages
from app.rag.chunker import chunk_document
from app.rag.embeddings import get_embedding_provider
from app.rag.parsers import ParsedDocument, parse_document
from app.repositories.document_repo import DocumentRepository
from app.repositories.task_repo import TaskRepository
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

PARSE_TASK_NAME = "app.workers.tasks.parse_document_task"
EMBED_TASK_NAME = "app.workers.tasks.embed_document_task"

# Progress checkpoints written to ``tasks.progress`` / ``documents.progress``.
# Embedding advances linearly between CHUNKED (80) and EMBEDDED (95) as batches
# complete, so large documents show visible movement during the slow stage.
PROGRESS_STARTED = 5
PROGRESS_PARSED = 30
PROGRESS_CLEANED = 60
PROGRESS_CHUNKED = 80
PROGRESS_EMBEDDED = 95
PROGRESS_COMPLETED = 100

# Error messages are stored in a VARCHAR(1024) column.
MAX_ERROR_LENGTH = 1024

DOCUMENT_STATUS_PROCESSING = "processing"
DOCUMENT_STATUS_COMPLETED = "completed"
DOCUMENT_STATUS_FAILED = "failed"

CHUNK_SIZE = 800
CHUNK_OVERLAP = 120


class DocumentParseError(RuntimeError):
    """Raised/returned when the parsing pipeline fails."""


class DocumentEmbedError(RuntimeError):
    """Raised/returned when the embedding pipeline fails."""


# ---------------------------------------------------------------------------
# Shared embedding stage
# ---------------------------------------------------------------------------
def _embed_and_persist(
    db: Any,
    doc_repo: DocumentRepository,
    task_repo: TaskRepository,
    document: Document,
    task_row: Task,
    chunk_rows: list[DocumentChunk],
    *,
    start_progress: int,
    end_progress: int,
) -> Optional[str]:
    """Embed chunk rows in batches, checkpointing progress after each batch.

    Vectors are written onto the ORM rows and committed together with the
    progress update, so a long embedding run is resumable at batch granularity
    (a later re-embed simply overwrites). Returns the provider signature stored
    on ``documents.embedding_model``, or ``None`` when there is nothing to
    embed. Provider failures propagate to the caller's error handling.
    """
    if not chunk_rows:
        return None

    provider = get_embedding_provider()
    batch_size = max(1, settings.EMBEDDING_BATCH_SIZE)
    total = len(chunk_rows)
    done = 0
    logger.info(
        "Embedding %d chunks of document %s with %s (batch=%d)",
        total,
        document.id,
        provider.signature,
        batch_size,
    )
    for start in range(0, total, batch_size):
        window = chunk_rows[start : start + batch_size]
        vectors = provider.embed_documents([chunk.content for chunk in window])
        for chunk, vector in zip(window, vectors):
            chunk.embedding = vector
        done += len(window)
        progress = start_progress + (end_progress - start_progress) * done // total
        _checkpoint(db, doc_repo, task_repo, document, task_row, progress)
    return provider.signature


# ---------------------------------------------------------------------------
# Parse pipeline (Phase 2 + Phase 3 embedding stage)
# ---------------------------------------------------------------------------
def run_parse_task_inline(task_row_id: int) -> dict[str, Any]:
    """Run the full parse pipeline for ``task_row_id`` in the current process.

    Opens its own :class:`~sqlalchemy.orm.Session` — a worker must never
    reuse a request session.

    Returns:
        ``{"status": "completed", "document_id", "page_count", "chunk_count",
        "character_count", "file_type", "embedding_model"}`` on success, or
        ``{"status": "failed", "error": ...}`` when the pipeline fails (the
        failure is persisted on both the task and the document first, and the
        function does not raise, so an API fallback call cannot 500).
    """
    db = SessionLocal()
    try:
        task_repo = TaskRepository(db)
        doc_repo = DocumentRepository(db)

        task_row = task_repo.get_by_id(task_row_id)
        if task_row is None:
            logger.warning("Parse task %s not found; nothing to do", task_row_id)
            return {"status": "skipped", "error": f"task {task_row_id} not found"}

        if task_row.document_id is None:
            return _record_failure(
                db, task_repo, doc_repo, task_row, None,
                f"task {task_row_id} has no document_id",
            )

        document = doc_repo.get_by_id(task_row.document_id)
        if document is None:
            return _record_failure(
                db, task_repo, doc_repo, task_row, None,
                f"document {task_row.document_id} not found",
            )

        # --- checkpoint 5%: task running, document processing -------------
        task_repo.set_running(task_row)
        task_repo.update(task_row, progress=PROGRESS_STARTED)
        doc_repo.update(
            document,
            status=DOCUMENT_STATUS_PROCESSING,
            progress=PROGRESS_STARTED,
        )
        db.commit()

        try:
            source_path = _resolve_storage_path(document.storage_path)
            logger.info(
                "Parsing document %s (%s) for task %s",
                document.id,
                source_path,
                task_row_id,
            )
            parsed = parse_document(source_path, document.file_type)
            _checkpoint(db, doc_repo, task_repo, document, task_row, PROGRESS_PARSED)

            pages = clean_pages(parsed.pages)
            character_count = sum(len(page.text) for page in pages)
            cleaned = ParsedDocument(
                pages=pages,
                page_count=len(pages),
                character_count=character_count,
                file_type=parsed.file_type,
            )
            _checkpoint(db, doc_repo, task_repo, document, task_row, PROGRESS_CLEANED)

            chunks = chunk_document(
                cleaned, chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
            )
            _checkpoint(db, doc_repo, task_repo, document, task_row, PROGRESS_CHUNKED)

            chunk_rows = _to_chunk_rows(document, chunks)
            doc_repo.replace_chunks(document.id, chunk_rows)

            # Phase 3: embedding is part of ingestion — a document without
            # vectors would be invisible to RAG search.
            embedding_model = _embed_and_persist(
                db, doc_repo, task_repo, document, task_row, chunk_rows,
                start_progress=PROGRESS_CHUNKED,
                end_progress=PROGRESS_EMBEDDED,
            )

            doc_repo.update(
                document,
                status=DOCUMENT_STATUS_COMPLETED,
                progress=PROGRESS_COMPLETED,
                page_count=cleaned.page_count,
                chunk_count=len(chunks),
                character_count=cleaned.character_count,
            )
            document.embedding_model = embedding_model
            document.error_message = None  # repository update() ignores None
            result = {
                "document_id": document.id,
                "page_count": cleaned.page_count,
                "chunk_count": len(chunks),
                "character_count": cleaned.character_count,
                "file_type": cleaned.file_type,
                "embedding_model": embedding_model,
            }
            task_repo.set_completed(task_row, result)
            db.commit()
            logger.info("Document %s parsed and embedded: %s", document.id, result)
            return {"status": "completed", **result}
        except Exception as exc:  # noqa: BLE001 - recorded and reported
            logger.exception("Parse pipeline failed for task %s", task_row_id)
            db.rollback()
            return _record_failure(
                db, task_repo, doc_repo, task_row, document, str(exc)
            )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Embed pipeline (Phase 3 re-embedding)
# ---------------------------------------------------------------------------
def run_embed_task_inline(task_row_id: int) -> dict[str, Any]:
    """(Re-)generate vectors for every chunk of the task's document.

    Used after provider/model switches (``documents.embedding_model`` goes
    stale) and as the recovery path when the embedding stage of a parse run
    failed — chunks survive a failed embedding, so only the vectors need
    regenerating.
    """
    db = SessionLocal()
    try:
        task_repo = TaskRepository(db)
        doc_repo = DocumentRepository(db)

        task_row = task_repo.get_by_id(task_row_id)
        if task_row is None:
            logger.warning("Embed task %s not found; nothing to do", task_row_id)
            return {"status": "skipped", "error": f"task {task_row_id} not found"}

        if task_row.document_id is None:
            return _record_failure(
                db, task_repo, doc_repo, task_row, None,
                f"task {task_row_id} has no document_id",
            )

        document = doc_repo.get_by_id(task_row.document_id)
        if document is None:
            return _record_failure(
                db, task_repo, doc_repo, task_row, None,
                f"document {task_row.document_id} not found",
            )

        task_repo.set_running(task_row)
        task_repo.update(task_row, progress=PROGRESS_STARTED)
        doc_repo.update(
            document,
            status=DOCUMENT_STATUS_PROCESSING,
            progress=PROGRESS_STARTED,
        )
        db.commit()

        try:
            chunk_rows = doc_repo.list_chunks_by_document(document.id)
            if not chunk_rows:
                raise DocumentEmbedError(
                    "document has no chunks; it must be parsed before embedding"
                )

            embedding_model = _embed_and_persist(
                db, doc_repo, task_repo, document, task_row, chunk_rows,
                start_progress=PROGRESS_STARTED,
                end_progress=PROGRESS_EMBEDDED,
            )

            document.embedding_model = embedding_model
            doc_repo.update(
                document,
                status=DOCUMENT_STATUS_COMPLETED,
                progress=PROGRESS_COMPLETED,
            )
            document.error_message = None
            result = {
                "document_id": document.id,
                "chunk_count": len(chunk_rows),
                "embedding_model": embedding_model,
            }
            task_repo.set_completed(task_row, result)
            db.commit()
            logger.info("Document %s embedded: %s", document.id, result)
            return {"status": "completed", **result}
        except Exception as exc:  # noqa: BLE001 - recorded and reported
            logger.exception("Embed pipeline failed for task %s", task_row_id)
            db.rollback()
            return _record_failure(
                db, task_repo, doc_repo, task_row, document, str(exc)
            )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------
def _celery_wrapper(task_name: str):
    """Build a bounded-retry Celery task over one of the inline pipelines.

    Retries are capped by ``max_retries=2``; once exhausted the persisted
    failure dict is returned instead of re-raising, so a poison document can
    never loop forever. In eager mode retries are skipped entirely.
    """

    runner = {
        PARSE_TASK_NAME: run_parse_task_inline,
        EMBED_TASK_NAME: run_embed_task_inline,
    }[task_name]

    error_cls = (
        DocumentParseError if task_name == PARSE_TASK_NAME else DocumentEmbedError
    )

    @celery_app.task(name=task_name, bind=True, max_retries=2, default_retry_delay=10)
    def wrapper(self: CeleryTask, task_id: int) -> dict[str, Any]:
        outcome = runner(task_id)
        if outcome.get("status") != "failed":
            return outcome

        error = error_cls(str(outcome.get("error") or "document task failed"))
        retries = int(getattr(self.request, "retries", 0) or 0)

        if bool(getattr(self.request, "is_eager", False)):
            logger.warning(
                "%s failed in eager mode; not retrying: %s", task_name, error
            )
            return outcome

        max_retries = int(getattr(self, "max_retries", 0) or 0)
        if retries >= max_retries:
            logger.error(
                "%s failed after %d retries; giving up: %s",
                task_name,
                retries,
                error,
            )
            return outcome

        logger.warning(
            "%s failed (attempt %d/%d), retrying in %ss: %s",
            task_name,
            retries + 1,
            max_retries + 1,
            self.default_retry_delay,
            error,
        )
        raise self.retry(exc=error)

    return wrapper


parse_document_task = _celery_wrapper(PARSE_TASK_NAME)
embed_document_task = _celery_wrapper(EMBED_TASK_NAME)


# ---------------------------------------------------------------------------
# Dispatch helpers for the API layer
# ---------------------------------------------------------------------------
def requires_celery_worker() -> bool:
    """True when tasks must go through a Celery worker.

    False means "no worker available" (eager/demo mode), so the caller should
    use the matching ``run_*_inline`` function.
    """
    return not settings.CELERY_TASK_ALWAYS_EAGER


def _dispatch(celery_task: Any, task_row_id: int, inline_runner: Any) -> str | None:
    """Queue ``task_row_id`` on ``celery_task``, falling back to inline.

    Returns the Celery task id when the job was published, or ``None`` when it
    was executed inline. Never raises for an unreachable broker: that must not
    break the API request that triggered the work.
    """
    if not requires_celery_worker():
        logger.info(
            "CELERY_TASK_ALWAYS_EAGER is set; running task %s inline", task_row_id
        )
        inline_runner(task_row_id)
        return None

    try:
        async_result = celery_task.delay(task_row_id)
    except Exception as exc:  # noqa: BLE001 - broker down / misconfigured URL
        logger.warning(
            "Celery broker unavailable (%s); running task %s inline instead",
            exc,
            task_row_id,
        )
        inline_runner(task_row_id)
        return None

    return getattr(async_result, "id", None)


def dispatch_parse_task(task_row_id: int) -> str | None:
    """Queue the parse task, falling back to an inline run."""
    return _dispatch(parse_document_task, task_row_id, run_parse_task_inline)


def dispatch_embed_task(task_row_id: int) -> str | None:
    """Queue the (re-)embed task, falling back to an inline run."""
    return _dispatch(embed_document_task, task_row_id, run_embed_task_inline)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _resolve_storage_path(storage_path: str) -> Path:
    """Absolute path of an uploaded file.

    ``storage_path`` is normally relative to ``settings.upload_dir``; an
    absolute value is honoured as-is.
    """
    path = Path(storage_path)
    if path.is_absolute():
        return path
    return Path(settings.upload_dir) / path


def _to_chunk_rows(document: Document, chunks: list[Any]) -> list[DocumentChunk]:
    """Map chunker output onto unsaved :class:`DocumentChunk` ORM rows."""
    return [
        DocumentChunk(
            document_id=document.id,
            knowledge_base_id=document.knowledge_base_id,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            page_number=chunk.page_number,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            token_estimate=chunk.token_estimate,
        )
        for chunk in chunks
    ]


def _checkpoint(
    db: Any,
    doc_repo: DocumentRepository,
    task_repo: TaskRepository,
    document: Document,
    task_row: Task,
    progress: int,
) -> None:
    """Persist progress on both rows and commit, so the UI sees movement."""
    task_repo.update(task_row, progress=progress)
    doc_repo.update(document, progress=progress)
    db.commit()


def _record_failure(
    db: Any,
    task_repo: TaskRepository,
    doc_repo: DocumentRepository,
    task_row: Optional[Task],
    document: Optional[Document],
    message: str,
) -> dict[str, Any]:
    """Persist a failed task + document and return the failure dict."""
    text = (message or "document task failed")[:MAX_ERROR_LENGTH]
    try:
        if task_row is not None:
            task_repo.set_failed(task_row, text)
        if document is not None:
            doc_repo.update(
                document,
                status=DOCUMENT_STATUS_FAILED,
                error_message=text,
            )
        db.commit()
    except Exception:  # noqa: BLE001 - never mask the original failure
        db.rollback()
        logger.exception("Could not persist failure for task %s", getattr(task_row, "id", None))
    return {
        "status": "failed",
        "task_id": getattr(task_row, "id", None),
        "document_id": getattr(document, "id", None),
        "error": text,
    }
