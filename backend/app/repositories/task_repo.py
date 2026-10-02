"""Task repository — pure DB access, no business logic."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.knowledge import Task


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TaskRepository:
    """Encapsulates all SQL access for the ``tasks`` table.

    The ``set_*`` helpers only mutate the ORM instance; committing (and the
    progress/error bookkeeping that goes with it) is the caller's job, so a
    worker can advance a task together with the document it belongs to in one
    transaction.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ---------- lookups ----------
    def get_by_id(self, task_id: int) -> Optional[Task]:
        return self.db.get(Task, task_id)

    def list_by_user(
        self,
        user_id: int,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Task]:
        stmt = select(Task).where(Task.user_id == user_id)
        if status is not None:
            stmt = stmt.where(Task.status == status)
        stmt = stmt.order_by(Task.id.desc()).offset(skip).limit(limit)
        return list(self.db.execute(stmt).scalars().all())

    def count_by_user(self, user_id: int) -> int:
        stmt = select(func.count()).select_from(Task).where(Task.user_id == user_id)
        return int(self.db.execute(stmt).scalar_one())

    def count_by_status(self, user_id: int, status: str) -> int:
        stmt = select(func.count()).select_from(Task).where(
            Task.user_id == user_id, Task.status == status
        )
        return int(self.db.execute(stmt).scalar_one())

    # ---------- mutations ----------
    def create(
        self,
        *,
        user_id: int,
        task_type: str,
        document_id: Optional[int] = None,
        payload: Optional[dict[str, Any]] = None,
        status: str = "pending",
    ) -> Task:
        task = Task(
            user_id=user_id,
            type=task_type,
            document_id=document_id,
            payload=payload,
            status=status,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def update(self, task: Task, **fields: Any) -> Task:
        """Apply a partial update. ``None`` values are ignored."""
        for key, value in fields.items():
            if value is not None:
                setattr(task, key, value)
        return task

    def delete(self, task: Task) -> None:
        self.db.delete(task)

    # ---------- state transitions ----------
    def set_running(self, task: Task) -> Task:
        task.status = "running"
        if task.started_at is None:
            task.started_at = _utcnow()
        return task

    def set_completed(self, task: Task, result: Optional[dict[str, Any]] = None) -> Task:
        task.status = "completed"
        task.progress = 100
        task.finished_at = _utcnow()
        if result is not None:
            task.result = result
        return task

    def set_failed(self, task: Task, message: str) -> Task:
        task.status = "failed"
        task.error_message = message[:1024]
        task.finished_at = _utcnow()
        return task
