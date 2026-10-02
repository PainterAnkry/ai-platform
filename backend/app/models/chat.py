"""Conversation and message ORM models (Phase 4 chat history).

A conversation binds an optional knowledge base, so the same chat surface
covers plain LLM chat and grounded RAG Q&A. Messages persist both sides of
every exchange plus the retrieval citations shown in the UI, so the framework
requirement "对话历史" is real data, not a frontend-only list.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy import JSON as SA_JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Conversation(Base):
    """One chat thread owned by a single user."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), index=True, nullable=False
    )
    # Optional RAG grounding. SET NULL: deleting a knowledge base must not
    # delete the chat history, it just stops being grounded.
    knowledge_base_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("knowledge_bases.id", ondelete="SET NULL"),
        nullable=True,
    )

    title: Mapped[str] = mapped_column(String(128), default="新对话", nullable=False)
    # Filled in Phase 5 when a conversation is bound to an agent.
    agent_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Conversation id={self.id} user={self.user_id} title={self.title!r}>"


# roles stored on messages; "tool" rows arrive with Phase 5 agent runs.
MessageRole = Literal["user", "assistant", "system", "tool"]


class Message(Base):
    """One message inside a conversation."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # Citations shown under assistant messages (document/page/score list).
    sources: Mapped[Optional[list[Any]]] = mapped_column(SA_JSON, nullable=True)
    # Phase 5: raw tool_calls requested by the model.
    tool_calls: Mapped[Optional[dict[str, Any]]] = mapped_column(SA_JSON, nullable=True)
    # LLM provider signature that produced an assistant message.
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Message id={self.id} conv={self.conversation_id} role={self.role!r}>"
