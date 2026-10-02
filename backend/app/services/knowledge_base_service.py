"""Knowledge base business logic."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.knowledge import KnowledgeBase
from app.repositories.document_repo import DocumentRepository
from app.repositories.knowledge_repo import KnowledgeBaseRepository
from app.schemas.knowledge import (
    KnowledgeBaseCreate,
    KnowledgeBaseRead,
    KnowledgeBaseUpdate,
)
from app.services.storage import remove_stored_file


class KnowledgeBaseService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = KnowledgeBaseRepository(db)
        self.doc_repo = DocumentRepository(db)

    # ---------- reads ----------
    def list_for_user(self, user_id: int, skip: int = 0, limit: int = 50) -> list[KnowledgeBaseRead]:
        items = self.repo.list_by_user(user_id, skip=skip, limit=limit)
        return [self._to_read(kb) for kb in items]

    def get_for_user(self, kb_id: int, user_id: int) -> KnowledgeBase:
        """Fetch a knowledge base the caller owns, or 404.

        Returning 404 (not 403) for someone else's row avoids leaking that
        the id exists at all.
        """
        kb = self.repo.get_by_id(kb_id)
        if kb is None or kb.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Knowledge base not found",
            )
        return kb

    def get_read(self, kb_id: int, user_id: int) -> KnowledgeBaseRead:
        return self._to_read(self.get_for_user(kb_id, user_id))

    # ---------- writes ----------
    def create(self, payload: KnowledgeBaseCreate, user_id: int) -> KnowledgeBaseRead:
        kb = self.repo.create(
            user_id=user_id,
            name=payload.name.strip(),
            description=(payload.description or "").strip(),
        )
        return self._to_read(kb)

    def update(
        self, kb_id: int, payload: KnowledgeBaseUpdate, user_id: int
    ) -> KnowledgeBaseRead:
        kb = self.get_for_user(kb_id, user_id)
        fields: dict = {}
        if payload.name is not None:
            fields["name"] = payload.name.strip()
        if payload.description is not None:
            fields["description"] = payload.description.strip()
        if fields:
            kb = self.repo.update(kb, **fields)
        return self._to_read(kb)

    def delete(self, kb_id: int, user_id: int) -> None:
        kb = self.get_for_user(kb_id, user_id)
        # Remove the stored uploads first: the document/chunk/task rows cascade
        # away with the knowledge base, but their files on disk would otherwise
        # be orphaned forever.
        for document in self.doc_repo.list_by_kb(kb_id, limit=10_000):
            remove_stored_file(document.storage_path)
        self.repo.delete(kb)
        # Repositories do not commit; the service owns the transaction
        # boundary so a 204 response always means the row is gone.
        self.db.commit()

    # ---------- helpers ----------
    def _to_read(self, kb: KnowledgeBase) -> KnowledgeBaseRead:
        read = KnowledgeBaseRead.model_validate(kb)
        read.document_count = self.doc_repo.count_by_kb(kb.id)
        return read
