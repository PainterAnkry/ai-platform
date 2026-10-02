"""Background task Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import Field

from app.schemas.common import ORMModel

TaskStatus = Literal["pending", "running", "completed", "failed"]

# Task type discriminator written by the services layer, e.g. "document.parse".
TASK_TYPE_DOCUMENT_PARSE = "document.parse"
# Phase 3: (re-)generate vectors for an already-parsed document's chunks,
# e.g. after the embedding provider or model changed.
TASK_TYPE_DOCUMENT_EMBED = "document.embed"


class TaskCreate(ORMModel):
    type: str = Field(min_length=1, max_length=64)
    document_id: Optional[int] = None
    payload: Optional[dict[str, Any]] = None


class TaskUpdate(ORMModel):
    """Partial update — only supplied fields are applied."""

    status: Optional[TaskStatus] = None
    progress: Optional[int] = Field(default=None, ge=0, le=100)
    celery_task_id: Optional[str] = Field(default=None, max_length=128)
    result: Optional[dict[str, Any]] = None
    error_message: Optional[str] = Field(default=None, max_length=1024)


class TaskRead(ORMModel):
    id: int
    document_id: Optional[int] = None
    type: str
    status: TaskStatus
    progress: int
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime
    result: Optional[dict[str, Any]] = None
