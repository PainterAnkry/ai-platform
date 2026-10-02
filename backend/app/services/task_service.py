"""Task query business logic — read-only progress tracking."""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.repositories.task_repo import TaskRepository
from app.schemas.task import TaskRead


class TaskService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = TaskRepository(db)

    def list_for_user(
        self,
        user_id: int,
        task_status: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[TaskRead]:
        tasks = self.repo.list_by_user(user_id, status=task_status, skip=skip, limit=limit)
        return [TaskRead.model_validate(t) for t in tasks]

    def get_for_user(self, task_id: int, user_id: int) -> TaskRead:
        task = self.repo.get_by_id(task_id)
        if task is None or task.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )
        return TaskRead.model_validate(task)
