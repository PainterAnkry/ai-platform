"""Pytest fixtures for the backend."""

from __future__ import annotations

import os

# Ensure tests can find the ``app`` package even when invoked from elsewhere.
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# Force a deterministic test environment before importing the app.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-please-change-me-1234567890")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("REDIS_HOST", "localhost")


@pytest.fixture()
def sqlite_engine(tmp_path: Path):
    """A file-backed SQLite engine with foreign keys enforced.

    Uses the same wiring as the zero-install preview (``PRAGMA
    foreign_keys=ON``), so tests exercise the behaviour the local demo gets
    instead of PostgreSQL-only semantics.
    """
    import app.models  # noqa: F401  — registers every model on Base.metadata
    from app.core.database import Base, enable_sqlite_foreign_keys

    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def sqlite_session_factory(sqlite_engine):
    """Session factory for the SQLite engine (used to stub ``SessionLocal``)."""
    return sessionmaker(bind=sqlite_engine, expire_on_commit=False, future=True)


@pytest.fixture()
def sqlite_session(sqlite_session_factory) -> Session:
    db = sqlite_session_factory()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def api(sqlite_session: Session, sqlite_session_factory, monkeypatch: pytest.MonkeyPatch):
    """TestClient wired to the per-test SQLite database.

    ``get_db`` reads ``SessionLocal`` at call time, so repointing it here makes
    the whole HTTP layer exercise the same database the fixtures seeded.
    """
    import app.core.database as database
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(database, "SessionLocal", sqlite_session_factory)
    return TestClient(app)
