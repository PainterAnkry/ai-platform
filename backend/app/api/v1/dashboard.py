"""Dashboard router — home view stats, 7-day series and recent tasks."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DBSession
from app.schemas.dashboard import DashboardResponse
from app.services.dashboard_service import DashboardService

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get(
    "",
    response_model=DashboardResponse,
    summary="Dashboard overview: stats, 7-day AI call series, recent tasks",
)
def get_dashboard(db: DBSession, current_user: CurrentUser) -> DashboardResponse:
    return DashboardService(db, user_id=current_user.id).get_overview()
