"""Celery application instance.

Importing this module is side-effect-light: it reads settings and configures
the app object, but never opens a broker connection. Connection attempts only
happen when a task is published (``delay``/``apply_async``) or a worker
starts, so the API can import it with Redis down.
"""

from __future__ import annotations

from celery import Celery

from app.core.config import settings

# Timeouts that make a *Redis* broker fail fast.
#
# ``socket_connect_timeout`` / ``socket_timeout`` / ``retry_policy`` are Redis
# transport options; other transports (RabbitMQ, SQS, the SQLAlchemy test
# transport, ...) hand unknown keys straight to their driver or engine, where
# they raise. So these are applied only when the URL really is Redis — see
# :func:`transport_options_for`.
_REDIS_TRANSPORT_OPTIONS: dict = {
    "socket_connect_timeout": 2,
    "socket_timeout": 2,
    "max_retries": 0,
    "retry_policy": {"max_retries": 0},
}


def transport_options_for(url: str) -> dict:
    """Transport options appropriate for ``url``.

    Returns the Redis fail-fast options for ``redis://`` / ``rediss://`` URLs
    and an empty mapping for every other transport.
    """
    if url.startswith(("redis://", "rediss://")):
        return dict(_REDIS_TRANSPORT_OPTIONS)
    return {}


broker_url = settings.celery_broker_url
result_backend_url = settings.celery_result_backend

celery_app = Celery(
    "ai_platform",
    broker=broker_url,
    backend=result_backend_url,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_hijack_root_logger=False,
    result_expires=3600,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Queue name used by ``celery -A app.workers.celery_app worker``.
    task_default_queue="ai_platform",
    task_default_exchange="ai_platform",
    task_default_routing_key="ai_platform",
    # --- fail fast when the broker is unreachable ---
    # The API falls back to inline parsing when the broker is down, so Celery
    # must give up quickly instead of blocking the upload request. Celery's
    # default is 100 retries, which hangs the request for minutes.
    broker_connection_max_retries=0,
    broker_connection_retry=False,
    broker_connection_retry_on_startup=False,
    redis_socket_connect_timeout=2,
    redis_socket_timeout=2,
    redis_retry_on_timeout=False,
    broker_transport_options=transport_options_for(broker_url),
    result_backend_transport_options=transport_options_for(result_backend_url),
)

# True = execute tasks inline in the caller process (demo / no-worker mode).
celery_app.conf.task_always_eager = settings.CELERY_TASK_ALWAYS_EAGER

celery_app.conf.include = ["app.workers.tasks"]

__all__ = ["celery_app", "transport_options_for"]
