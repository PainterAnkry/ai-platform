"""Document upload + ingestion business logic.

Upload is deliberately synchronous up to the point of persisting the file and
creating a ``tasks`` row; all parsing work is dispatched to a Celery worker so
the request returns immediately (the framework requires long-running work to
stay off the FastAPI request thread).
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.knowledge import Document, Task
from app.repositories.document_repo import DocumentRepository
from app.repositories.knowledge_repo import KnowledgeBaseRepository
from app.repositories.task_repo import TaskRepository
from app.schemas.knowledge import (
    ALLOWED_FILE_TYPES,
    DocumentRead,
)
from app.schemas.task import TASK_TYPE_DOCUMENT_EMBED, TASK_TYPE_DOCUMENT_PARSE
from app.services.knowledge_base_service import KnowledgeBaseService
from app.services.storage import remove_stored_file

logger = logging.getLogger(__name__)

# Suffixes for the 8-char random token in stored filenames, so an uploaded
# "report.pdf" never overwrites a previous "report.pdf".
_STORAGE_CHUNK = 2


class DocumentService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = DocumentRepository(db)
        self.kb_repo = KnowledgeBaseRepository(db)
        self.task_repo = TaskRepository(db)
        self.kb_service = KnowledgeBaseService(db)

    # ---------- reads ----------
    def list_for_kb(
        self, kb_id: int, user_id: int, skip: int = 0, limit: int = 100
    ) -> list[DocumentRead]:
        self.kb_service.get_for_user(kb_id, user_id)  # ownership check
        docs = self.repo.list_by_kb(kb_id, skip=skip, limit=limit)
        return [DocumentRead.model_validate(d) for d in docs]

    def get_for_user(self, doc_id: int, user_id: int) -> Document:
        doc = self.repo.get_by_id(doc_id)
        if doc is None or doc.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
            )
        return doc

    # ---------- upload ----------
    def upload(
        self,
        *,
        kb_id: int,
        user_id: int,
        filename: str,
        content_type: Optional[str],
        data: bytes,
    ) -> tuple[DocumentRead, Task]:
        """Persist an uploaded file and queue it for parsing.

        Returns the created document and its task row. Raises 400 for an
        unsupported/empty file, 413 when the payload exceeds the configured
        limit, 404 when the knowledge base is not the caller's.
        """
        self.kb_service.get_for_user(kb_id, user_id)  # 404 if not owned

        safe_name = os.path.basename(filename or "").strip()
        if not safe_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Missing filename"
            )

        file_type = os.path.splitext(safe_name)[1].lstrip(".").lower()
        if file_type not in ALLOWED_FILE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Unsupported file type '{file_type or 'unknown'}'. "
                    f"Allowed: {', '.join(sorted(ALLOWED_FILE_TYPES))}"
                ),
            )

        if not data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file"
            )
        if len(data) > settings.MAX_UPLOAD_SIZE_BYTES:
            limit_mb = settings.MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds the {limit_mb} MB limit",
            )

        # Store under <upload_dir>/<user_id>/<random><ext> so names never collide.
        user_dir = os.path.join(settings.upload_dir, str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        stored_name = f"{uuid.uuid4().hex}.{file_type}"
        absolute_path = os.path.join(user_dir, stored_name)
        try:
            with open(absolute_path, "wb") as fh:
                fh.write(data)
        except OSError as exc:
            logger.exception("Failed to persist upload %s", safe_name)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Could not store the uploaded file: {exc}",
            ) from exc

        storage_path = os.path.join(str(user_id), stored_name)

        document = self.repo.create(
            knowledge_base_id=kb_id,
            user_id=user_id,
            filename=safe_name,
            file_type=file_type,
            file_size=len(data),
            storage_path=storage_path,
        )
        task = self.task_repo.create(
            user_id=user_id,
            document_id=document.id,
            task_type=TASK_TYPE_DOCUMENT_PARSE,
            payload={
                "document_id": document.id,
                "storage_path": storage_path,
                "file_type": file_type,
                "filename": safe_name,
            },
        )

        return DocumentRead.model_validate(document), task

    # ---------- dispatch ----------
    async def dispatch_parse(self, task: Task, *, broker_ok: bool | None = None) -> str:
        """Hand the parse task to Celery, falling back to inline execution.

        Args:
            task: the freshly created ingestion task.
            broker_ok: result of a cached Redis probe. ``False`` skips Celery
                entirely, which avoids paying several seconds of connect
                timeouts on every upload when no broker is configured.

        Returns:
            ``"queued"`` when a worker will pick the task up, ``"inline"``
            when it was parsed in this process.
        """
        # Imported lazily so the API boots even without the Celery extras.
        try:
            from app.workers.tasks import dispatch_parse_task, run_parse_task_inline
        except ImportError as exc:  # pragma: no cover - depends on env
            logger.warning("Celery worker module unavailable (%s); running inline", exc)
            run_parse_task_inline(task.id)
            return "inline"

        if broker_ok is False:
            logger.info("No Celery broker; parsing task %s inline", task.id)
            run_parse_task_inline(task.id)
            return "inline"

        # dispatch_parse_task also degrades to inline if the broker is down.
        celery_id = dispatch_parse_task(task.id)
        if celery_id:
            refreshed = self.task_repo.get_by_id(task.id)
            if refreshed is not None and refreshed.celery_task_id != celery_id:
                refreshed.celery_task_id = celery_id
                self.db.commit()
            return "queued"
        return "inline"

    # ---------- Phase 3: (re-)embedding ----------
    def create_embed_task(self, doc_id: int, user_id: int) -> Task:
        """Create a ``document.embed`` task for an already-parsed document.

        Used when the embedding provider/model changed (vectors went stale) or
        when the embedding stage of a parse run failed. Raises 400 when the
        document has no chunks and 409 while it is still being processed.
        """
        doc = self.get_for_user(doc_id, user_id)
        if doc.status == "processing" or doc.status == "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Document is still being processed; try again later",
            )
        if doc.chunk_count == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Document has no chunks to embed",
            )
        return self.task_repo.create(
            user_id=user_id,
            document_id=doc.id,
            task_type=TASK_TYPE_DOCUMENT_EMBED,
            payload={
                "document_id": doc.id,
                "filename": doc.filename,
                "reason": "manual re-embed",
            },
        )

    async def dispatch_embed(self, task: Task, *, broker_ok: bool | None = None) -> str:
        """Hand the embed task to Celery, falling back to inline execution.

        Mirrors :meth:`dispatch_parse` — see that method for the rationale.
        """
        try:
            from app.workers.tasks import dispatch_embed_task, run_embed_task_inline
        except ImportError as exc:  # pragma: no cover - depends on env
            logger.warning("Celery worker module unavailable (%s); running inline", exc)
            run_embed_task_inline(task.id)
            return "inline"

        if broker_ok is False:
            logger.info("No Celery broker; embedding task %s inline", task.id)
            run_embed_task_inline(task.id)
            return "inline"

        celery_id = dispatch_embed_task(task.id)
        if celery_id:
            refreshed = self.task_repo.get_by_id(task.id)
            if refreshed is not None and refreshed.celery_task_id != celery_id:
                refreshed.celery_task_id = celery_id
                self.db.commit()
            return "queued"
        return "inline"

    # ---------- delete ----------
    def delete(self, doc_id: int, user_id: int) -> None:
        doc = self.get_for_user(doc_id, user_id)
        # Best-effort cleanup of the stored file; the DB row goes either way.
        remove_stored_file(doc.storage_path)

        self.repo.delete(doc)
        # Repositories deliberately do not commit, so the service owns the
        # transaction boundary for destructive operations.
        self.db.commit()
