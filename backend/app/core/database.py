"""SQLAlchemy engine, session factory and Base class declaration."""

from __future__ import annotations

from typing import Any, Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _engine_options() -> dict:
    """Engine kwargs that differ between SQLite and PostgreSQL.

    SQLite (local preview only) uses a different pool implementation and
    needs ``check_same_thread=False`` because FastAPI serves requests from a
    thread pool. Production/Compose always runs PostgreSQL.
    """
    if settings.is_sqlite:
        return {
            "connect_args": {"check_same_thread": False},
            "pool_pre_ping": True,
        }
    return {
        "pool_pre_ping": True,
        "pool_size": 5,
        "max_overflow": 10,
    }


def enable_sqlite_foreign_keys(target: Engine) -> None:
    """Turn on foreign-key enforcement for a SQLite engine.

    SQLite parses ``FOREIGN KEY ... ON DELETE CASCADE`` but ignores it unless
    ``PRAGMA foreign_keys`` is enabled *per connection* (the default is off,
    and it cannot be set from a migration). PostgreSQL always enforces it, so
    without this the zero-install SQLite preview silently diverges from
    production: deleting a knowledge base would leave its documents, chunks and
    tasks behind as orphans.

    Attaching the pragma to the ``connect`` event covers every pooled
    connection, including ones opened later.
    """

    @event.listens_for(target, "connect")
    def _set_sqlite_pragma(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


engine = create_engine(settings.database_url, future=True, **_engine_options())

if settings.is_sqlite:
    enable_sqlite_foreign_keys(engine)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a SQLAlchemy session.

    The session is closed automatically when the request finishes.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
