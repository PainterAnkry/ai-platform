"""Async task router — list tasks and poll progress."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DBSession
from app.schemas.task import TaskRead
from app.services.task_service import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])

TaskStatusFilter = Literal["pending", "running", "completed", "failed"]


@router.get(
    "",
    response_model=list[TaskRead],
    summary="List the caller's async tasks, newest first",
)
def list_tasks(
    db: DBSession,
    current_user: CurrentUser,
    status_filter: Optional[TaskStatusFilter] = Query(
        default=None,
        alias="status",
        description="Filter by lifecycle status",
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> list[TaskRead]:
    return TaskService(db).list_for_user(
        current_user.id, task_status=status_filter, skip=skip, limit=limit
    )


@router.get(
    "/{task_id}",
    response_model=TaskRead,
    summary="Fetch a single task (used for progress polling)",
)
def get_task(
    task_id: int,
    db: DBSession,
    current_user: CurrentUser,
) -> TaskRead:
    return TaskService(db).get_for_user(task_id, current_user.id)
