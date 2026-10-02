"""Knowledge base CRUD router."""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DBSession
from app.schemas.knowledge import (
    KnowledgeBaseCreate,
    KnowledgeBaseRead,
    KnowledgeBaseUpdate,
)
from app.services.knowledge_base_service import KnowledgeBaseService

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


@router.get(
    "",
    response_model=list[KnowledgeBaseRead],
    summary="List the caller's knowledge bases with document counts",
)
def list_knowledge_bases(
    db: DBSession,
    current_user: CurrentUser,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> list[KnowledgeBaseRead]:
    return KnowledgeBaseService(db).list_for_user(current_user.id, skip=skip, limit=limit)


@router.post(
    "",
    response_model=KnowledgeBaseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a knowledge base",
)
def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    db: DBSession,
    current_user: CurrentUser,
) -> KnowledgeBaseRead:
    return KnowledgeBaseService(db).create(payload, current_user.id)


@router.get(
    "/{kb_id}",
    response_model=KnowledgeBaseRead,
    summary="Fetch a single knowledge base",
)
def get_knowledge_base(
    kb_id: int,
    db: DBSession,
    current_user: CurrentUser,
) -> KnowledgeBaseRead:
    return KnowledgeBaseService(db).get_read(kb_id, current_user.id)


@router.patch(
    "/{kb_id}",
    response_model=KnowledgeBaseRead,
    summary="Update a knowledge base (partial)",
)
def update_knowledge_base(
    kb_id: int,
    payload: KnowledgeBaseUpdate,
    db: DBSession,
    current_user: CurrentUser,
) -> KnowledgeBaseRead:
    return KnowledgeBaseService(db).update(kb_id, payload, current_user.id)


@router.delete(
    "/{kb_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a knowledge base and its documents",
)
def delete_knowledge_base(
    kb_id: int,
    db: DBSession,
    current_user: CurrentUser,
) -> None:
    KnowledgeBaseService(db).delete(kb_id, current_user.id)
