"""Dashboard schemas.

Aggregates real rows from Phase 2 onwards (knowledge bases, documents, tasks).
``conversation_count`` and ``ai_call_count`` stay at 0 until Phase 4 introduces
those tables.
"""

from __future__ import annotations

from pydantic import Field

from app.schemas.common import ORMModel
from app.schemas.task import TaskRead


class DashboardStats(ORMModel):
    knowledge_base_count: int = Field(default=0, ge=0)
    document_count: int = Field(default=0, ge=0)
    conversation_count: int = Field(default=0, ge=0)
    ai_call_count: int = Field(default=0, ge=0)
    running_task_count: int = Field(default=0, ge=0)
    completed_task_count: int = Field(default=0, ge=0)


class DashboardTrendPoint(ORMModel):
    date: str = Field(description="ISO date (YYYY-MM-DD)")
    value: int = Field(ge=0)


class DashboardTrend(ORMModel):
    """7-day rolling counts; placeholder until Phase 4+ writes real data."""

    metric: str
    points: list[DashboardTrendPoint]


class DashboardResponse(ORMModel):
    stats: DashboardStats
    ai_call_trend: DashboardTrend
    task_status_breakdown: list[DashboardTrendPoint]
    # Newest few ingestion tasks, so the home view can show "最近任务 + 进度"
    # (framework §12) without a second request.
    recent_tasks: list[TaskRead] = Field(default_factory=list)