"""Logging configuration — structured and environment-aware."""

from __future__ import annotations

import sys

from app.core.config import settings

_CONFIGURED = False


def configure_logging() -> None:
    """Idempotent logging setup. Uses a simple stdout format suitable for
    container log collectors; structured JSON logging can be layered in later.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = settings.LOG_LEVEL.upper()
    handler_level = level if level in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"} else "INFO"

    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    import logging

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(handler_level)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(handler_level)
    stream_handler.setFormatter(logging.Formatter(fmt, datefmt))
    root.addHandler(stream_handler)

    # Reduce noise from common libs.
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "passlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True