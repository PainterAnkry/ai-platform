"""Health / readiness probes."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import DBSession
from app.core.redis import get_redis
from redis.asyncio import Redis

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", summary="Liveness + dependency probes")
async def health(
    db: DBSession,
    redis: Redis = Depends(get_redis),
) -> Dict[str, Any]:
    """Reports the status of the API itself and its dependencies.

    Always returns 200 (so probes don't tear down a half-broken stack);
    inspect ``components`` for individual subsystem state.
    """

    components: Dict[str, Any] = {}

    # Database
    try:
        db.execute(text("SELECT 1"))
        components["postgres"] = {"status": "ok"}
    except Exception as exc:  # pragma: no cover - depends on env
        components["postgres"] = {"status": "down", "error": str(exc)}

    # Redis
    try:
        ok = await redis.ping()
        components["redis"] = {"status": "ok" if ok else "down"}
    except Exception as exc:  # pragma: no cover
        components["redis"] = {"status": "down", "error": str(exc)}

    overall = all(c.get("status") == "ok" for c in components.values())
    return {
        "status": "ok" if overall else "degraded",
        "components": components,
    }