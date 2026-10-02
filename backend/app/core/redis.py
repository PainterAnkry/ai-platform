"""Async Redis client wrapper used as a cache/queue in later phases."""

from __future__ import annotations

import logging
import time
from typing import Optional

import redis.asyncio as redis_async
from redis.asyncio import Redis

from app.core.config import settings

logger = logging.getLogger(__name__)


class RedisClient:
    """Thin wrapper around ``redis.asyncio.Redis`` with lazy connection.

    We deliberately keep this minimal in Phase 1 (a simple cache/queue
    primitive). The ``Worker`` orchestration layer arrives in Phase 7.
    """

    def __init__(self) -> None:
        self._client: Optional[Redis] = None

    async def connect(self) -> None:
        if self._client is not None:
            return
        self._client = redis_async.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        try:
            await self._client.ping()
            logger.info("Redis connection established (%s)", settings.redis_url)
        except Exception as exc:  # pragma: no cover - depends on env
            logger.warning("Redis ping failed at startup: %s", exc)

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    @property
    def client(self) -> Redis:
        if self._client is None:
            raise RuntimeError("Redis client not initialised; call connect() first.")
        return self._client

    async def ping(self) -> bool:
        if self._client is None:
            await self.connect()
        try:
            return bool(await self.client.ping())
        except Exception:  # pragma: no cover
            return False


redis_client = RedisClient()

# Cached result of the last broker probe, used by the Phase 2 dispatch path to
# decide whether Celery is worth trying. Publishing to a dead broker costs
# seconds of connect timeouts per request, so we probe once and remember.
_broker_state: tuple[float, bool] | None = None
_BROKER_TTL_SECONDS = 30.0


async def broker_available(ttl: float = _BROKER_TTL_SECONDS) -> bool:
    """Cheap, cached "is Redis reachable?" check for sync callers.

    Returns True when Redis answered a PING within ``ttl`` seconds of the last
    probe. Used to short-circuit Celery dispatch in environments without a
    broker (local preview), where the fallback is inline execution.
    """
    global _broker_state

    now = time.monotonic()
    if _broker_state is not None and now - _broker_state[0] < ttl:
        return _broker_state[1]

    ok = await redis_client.ping()
    _broker_state = (now, ok)
    return ok


async def get_redis() -> Redis:
    """FastAPI dependency that yields the shared Redis client."""
    if redis_client._client is None:  # noqa: SLF001
        await redis_client.connect()
    return redis_client.client