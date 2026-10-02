"""Dashboard service.

Aggregates the real knowledge base / document / task rows, and since Phase 4
the conversation / AI-call counts and the 7-day AI call trend.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.chat import Conversation, Message
from app.models.knowledge import Document, KnowledgeBase, Task
from app.repositories.task_repo import TaskRepository
from app.schemas.dashboard import (
    DashboardResponse,
    DashboardStats,
    DashboardTrend,
    DashboardTrendPoint,
)
from app.schemas.task import TaskRead

# Task statuses surfaced in the pie chart, in display order.
_TASK_STATUSES = ("pending", "running", "completed", "failed")

# How many tasks the "最近任务" table on the dashboard shows.
RECENT_TASK_LIMIT = 5


def _last_seven_days(today: date | None = None) -> list[date]:
    today = today or date.today()
    return [today - timedelta(days=i) for i in range(6, -1, -1)]


class DashboardService:
    """Read-only aggregate stats for the home dashboard."""

    def __init__(self, db: Session, user_id: int | None = None) -> None:
        self.db = db
        self.user_id = user_id

    def get_overview(self) -> DashboardResponse:
        stats = self._stats()
        return DashboardResponse(
            stats=stats,
            ai_call_trend=DashboardTrend(
                metric="ai_calls",
                points=self._ai_call_trend(),
            ),
            task_status_breakdown=self._task_breakdown(),
            recent_tasks=self._recent_tasks(),
        )

    # ---------- internals ----------
    def _recent_tasks(self) -> list[TaskRead]:
        """Newest tasks for the current user (empty for an unscoped call)."""
        if self.user_id is None:
            return []
        rows = TaskRepository(self.db).list_by_user(
            self.user_id, limit=RECENT_TASK_LIMIT
        )
        return [TaskRead.model_validate(row) for row in rows]

    def _stats(self) -> DashboardStats:
        kb_stmt = select(func.count(KnowledgeBase.id))
        doc_stmt = select(func.count(Document.id))
        task_stmt = select(func.count(Task.id))

        if self.user_id is not None:
            kb_stmt = kb_stmt.where(KnowledgeBase.user_id == self.user_id)
            doc_stmt = doc_stmt.where(Document.user_id == self.user_id)
            task_stmt = task_stmt.where(Task.user_id == self.user_id)

        knowledge_base_count = self.db.execute(kb_stmt).scalar_one()
        document_count = self.db.execute(doc_stmt).scalar_one()
        total_tasks = self.db.execute(task_stmt).scalar_one()

        running = completed = 0
        for status_name, count in self._task_status_counts():
            if status_name == "running":
                running = count
            elif status_name == "completed":
                completed = count

        return DashboardStats(
            knowledge_base_count=knowledge_base_count,
            document_count=document_count,
            conversation_count=self._conversation_count(),
            ai_call_count=self._ai_call_count(),
            running_task_count=running,
            completed_task_count=completed,
        )

    def _conversation_count(self) -> int:
        if self.user_id is None:
            return 0
        return int(
            self.db.execute(
                select(func.count(Conversation.id)).where(
                    Conversation.user_id == self.user_id
                )
            ).scalar_one()
        )

    def _ai_call_count(self) -> int:
        """Assistant messages across the user's conversations."""
        if self.user_id is None:
            return 0
        return int(
            self.db.execute(
                select(func.count(Message.id))
                .join(Conversation, Conversation.id == Message.conversation_id)
                .where(Conversation.user_id == self.user_id, Message.role == "assistant")
            ).scalar_one()
        )

    def _ai_call_trend(self) -> list[DashboardTrendPoint]:
        """Assistant messages per local day over the last 7 days."""
        if self.user_id is None:
            days = _last_seven_days()
            return [DashboardTrendPoint(date=d.isoformat(), value=0) for d in days]

        # Bucket by the database's date() of the stored UTC timestamp. Good
        # enough for a trend chart; per-timezone precision is Phase 7 scope.
        day_expr = func.date(Message.created_at)
        start = datetime.now(timezone.utc) - timedelta(days=6)
        stmt = (
            select(day_expr, func.count(Message.id))
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Conversation.user_id == self.user_id,
                Message.role == "assistant",
                Message.created_at >= start,
            )
            .group_by(day_expr)
        )
        found = {
            str(day): int(count)
            for day, count in self.db.execute(stmt).all()
        }
        return [
            DashboardTrendPoint(date=d.isoformat(), value=found.get(d.isoformat(), 0))
            for d in _last_seven_days()
        ]

    def _task_status_counts(self) -> list[tuple[str, int]]:
        stmt = select(Task.status, func.count(Task.id)).group_by(Task.status)
        if self.user_id is not None:
            stmt = stmt.where(Task.user_id == self.user_id)
        rows = self.db.execute(stmt).all()
        found = {name: int(count) for name, count in rows}
        return [(name, found.get(name, 0)) for name in _TASK_STATUSES]

    def _task_breakdown(self) -> list[DashboardTrendPoint]:
        return [
            DashboardTrendPoint(date=name, value=count)
            for name, count in self._task_status_counts()
        ]
