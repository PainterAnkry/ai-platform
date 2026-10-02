"""Document upload / listing / deletion router."""

from __future__ import annotations

from fastapi import APIRouter, File, Query, UploadFile, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, DBSession
from app.core.redis import broker_available
from app.schemas.knowledge import DocumentRead
from app.schemas.task import TaskRead
from app.services.document_service import DocumentService

router = APIRouter(prefix="/knowledge-bases", tags=["documents"])


class DocumentUploadResponse(BaseModel):
    """Wraps the created document with its ingestion task.

    ``dispatch`` explains how the work was scheduled: ``queued`` means a
    Celery worker picked it up, ``inline`` means the API fell back to running
    the parse synchronously (e.g. no broker available in a local preview).
    """

    document: DocumentRead
    task: TaskRead
    dispatch: str


class DocumentEmbedResponse(BaseModel):
    """Wraps the re-embed response: document + ``document.embed`` task."""

    document: DocumentRead
    task: TaskRead
    dispatch: str


@router.get(
    "/{kb_id}/documents",
    response_model=list[DocumentRead],
    summary="List documents in a knowledge base",
)
def list_documents(
    kb_id: int,
    db: DBSession,
    current_user: CurrentUser,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> list[DocumentRead]:
    return DocumentService(db).list_for_kb(
        kb_id, current_user.id, skip=skip, limit=limit
    )


@router.post(
    "/{kb_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a PDF/Word/TXT file and queue it for parsing",
)
async def upload_document(
    kb_id: int,
    db: DBSession,
    current_user: CurrentUser,
    file: UploadFile = File(..., description="PDF / DOCX / TXT, max 25 MB"),
) -> DocumentUploadResponse:
    service = DocumentService(db)
    data: bytes = await file.read()

    document, task = service.upload(
        kb_id=kb_id,
        user_id=current_user.id,
        filename=file.filename or "",
        content_type=file.content_type,
        data=data,
    )
    # A cached Redis probe tells us whether Celery is worth publishing to; when
    # no broker exists we go straight to the inline parser.
    dispatch = await service.dispatch_parse(task, broker_ok=await broker_available())

    # An inline parse commits in a *different* session, so this request's
    # identity map still holds the pre-parse rows. Expire it so the response
    # reflects what the worker actually persisted.
    db.expire_all()

    # Re-read after dispatch so an inline run's completed state is reflected.
    return DocumentUploadResponse(
        document=DocumentRead.model_validate(service.repo.get_by_id(document.id)),
        task=TaskRead.model_validate(service.task_repo.get_by_id(task.id)),
        dispatch=dispatch,
    )


@router.delete(
    "/{kb_id}/documents/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document and its stored file",
)
def delete_document(
    kb_id: int,  # noqa: ARG001 - kept for a stable, resource-scoped URL
    doc_id: int,
    db: DBSession,
    current_user: CurrentUser,
) -> None:
    DocumentService(db).delete(doc_id, current_user.id)


@router.post(
    "/{kb_id}/documents/{doc_id}/reembed",
    response_model=DocumentEmbedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-generate vectors for a parsed document",
)
async def reembed_document(
    kb_id: int,  # noqa: ARG001 - kept for a stable, resource-scoped URL
    doc_id: int,
    db: DBSession,
    current_user: CurrentUser,
) -> DocumentEmbedResponse:
    """Queue a ``document.embed`` task for the document's existing chunks.

    Used after switching the embedding provider/model (vectors went stale) or
    as the recovery path when the embedding stage of a parse run failed.
    """
    service = DocumentService(db)
    task = service.create_embed_task(doc_id, current_user.id)
    dispatch = await service.dispatch_embed(task, broker_ok=await broker_available())

    # Inline runs commit in a different session; expire so the response
    # reflects what actually persisted.
    db.expire_all()
    return DocumentEmbedResponse(
        document=DocumentRead.model_validate(service.repo.get_by_id(doc_id)),
        task=TaskRead.model_validate(service.task_repo.get_by_id(task.id)),
        dispatch=dispatch,
    )


__all__ = ["router", "DocumentEmbedResponse", "DocumentUploadResponse"]
