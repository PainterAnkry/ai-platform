"""Knowledge base repository — pure DB access, no business logic."""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.knowledge import KnowledgeBase


class KnowledgeBaseRepository:
    """Encapsulates all SQL access for the ``knowledge_bases`` table."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ---------- lookups ----------
    def get_by_id(self, kb_id: int) -> Optional[KnowledgeBase]:
        return self.db.get(KnowledgeBase, kb_id)

    def list_by_user(
        self, user_id: int, skip: int = 0, limit: int = 100
    ) -> list[KnowledgeBase]:
        stmt = (
            select(KnowledgeBase)
            .where(KnowledgeBase.user_id == user_id)
            .order_by(KnowledgeBase.id.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())

    def count_by_user(self, user_id: int) -> int:
        stmt = select(func.count()).select_from(KnowledgeBase).where(
            KnowledgeBase.user_id == user_id
        )
        return int(self.db.execute(stmt).scalar_one())

    # ---------- mutations ----------
    def create(
        self,
        *,
        user_id: int,
        name: str,
        description: Optional[str] = "",
    ) -> KnowledgeBase:
        kb = KnowledgeBase(user_id=user_id, name=name, description=description)
        self.db.add(kb)
        self.db.commit()
        self.db.refresh(kb)
        return kb

    def update(self, kb: KnowledgeBase, **fields: Any) -> KnowledgeBase:
        """Apply a partial update. ``None`` values are ignored."""
        for key, value in fields.items():
            if value is not None:
                setattr(kb, key, value)
        return kb

    def delete(self, kb: KnowledgeBase) -> None:
        self.db.delete(kb)
