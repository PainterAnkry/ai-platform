"""Celery configuration guards.

The fail-fast broker timeouts are Redis-only knobs: other transports forward
unknown options to their own driver/engine, which raises (``create_engine()``
rejecting ``socket_connect_timeout`` for the SQLAlchemy transport, for example).
These tests pin the selection logic so a non-Redis broker stays usable.
"""

from __future__ import annotations

from app.workers.celery_app import celery_app, transport_options_for


def test_redis_urls_get_fail_fast_options() -> None:
    for url in ("redis://localhost:6379/1", "rediss://cache.internal:6380/2"):
        options = transport_options_for(url)
        assert options["socket_connect_timeout"] == 2
        assert options["max_retries"] == 0
        assert options["retry_policy"] == {"max_retries": 0}


def test_non_redis_transports_get_no_redis_options() -> None:
    for url in (
        "sqlalchemy+sqlite:///broker.db",
        "amqp://guest:guest@rabbitmq:5672//",
        "sqs://",
        "memory://",
    ):
        assert transport_options_for(url) == {}


def test_returned_options_are_a_copy() -> None:
    """Callers must not be able to mutate the shared defaults."""
    first = transport_options_for("redis://localhost:6379/1")
    first["socket_connect_timeout"] = 99
    assert transport_options_for("redis://localhost:6379/1")["socket_connect_timeout"] == 2


def test_app_still_fails_fast_on_the_default_broker() -> None:
    """The default (Redis db 1) keeps the retry-free configuration."""
    assert celery_app.conf.broker_connection_max_retries == 0
    assert celery_app.conf.broker_connection_retry is False
    assert celery_app.conf.task_always_eager is False


def test_queue_and_serializer_configuration() -> None:
    assert celery_app.conf.task_default_queue == "ai_platform"
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.accept_content == ["json"]
    assert "app.workers.tasks" in (celery_app.conf.include or [])
