"""Agent, tool registry and data-file ORM models (Phase 5)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import JSON as SA_JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Agent(Base):
    """A user-configured agent: persona + model override + enabled tools."""

    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Optional per-agent model override; empty = use the global LLM provider.
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    temperature: Mapped[Optional[float]] = mapped_column(nullable=True)
    # Enabled tool names, e.g. ["knowledge_search", "calculator"].
    tools: Mapped[Optional[list[Any]]] = mapped_column(SA_JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Agent id={self.id} name={self.name!r}>"


class Tool(Base):
    """Registry of tools an agent may call (framework: tools 表).

    Rows are synced from the code-defined registry on startup; the ``enabled``
    flag gates availability without a redeploy. MCP tools (Phase 6) will be
    appended to the same table with ``type='mcp'``.
    """

    __tablename__ = "tools"
    __table_args__ = (UniqueConstraint("name", name="uq_tools_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    # "builtin" today; "mcp" arrives in Phase 6.
    type: Mapped[str] = mapped_column(String(32), default="builtin", nullable=False)
    config: Mapped[Optional[dict[str, Any]]] = mapped_column(SA_JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Tool id={self.id} name={self.name!r} enabled={self.enabled}>"


class DataFile(Base):
    """An uploaded Excel/CSV file for the analysis agent."""

    __tablename__ = "data_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), index=True, nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(16), nullable=False)  # xlsx | csv
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    # Schema snapshot taken at upload time so the agent can pick tools
    # without re-reading the file.
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    column_names: Mapped[Optional[list[Any]]] = mapped_column(SA_JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<DataFile id={self.id} filename={self.filename!r}>"
