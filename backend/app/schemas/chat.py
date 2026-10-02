"""Conversation / chat-message Pydantic schemas (Phase 4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import Field, field_validator

from app.schemas.common import ORMModel

MAX_CHAT_CONTENT_LENGTH = 4000
# Multimodal attachments: data URLs are ~1.37x the binary size, so 5 MB of
# characters ≈ 3.6 MB of image.
MAX_CHAT_IMAGES = 4
MAX_CHAT_IMAGE_CHARS = 5 * 1024 * 1024
# Auto-title placeholder; replaced by the first user message's prefix.
DEFAULT_CONVERSATION_TITLE = "新对话"


class ConversationCreate(ORMModel):
    title: Optional[str] = Field(default=None, max_length=128)
    # Optional RAG grounding: messages in this conversation retrieve from the KB.
    knowledge_base_id: Optional[int] = None
    # Optional agent binding (Phase 5): messages run through the tool loop.
    agent_id: Optional[int] = None


class ConversationRename(ORMModel):
    title: str = Field(min_length=1, max_length=128)

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must not be blank")
        return stripped


class ConversationRead(ORMModel):
    id: int
    title: str
    knowledge_base_id: Optional[int] = None
    agent_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class MessageRead(ORMModel):
    id: int
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    sources: Optional[list[Any]] = None
    tool_calls: Optional[dict[str, Any]] = None
    model: Optional[str] = None
    created_at: datetime


class ConversationDetailRead(ConversationRead):
    messages: list[MessageRead] = Field(default_factory=list)


class ChatRequest(ORMModel):
    content: str = Field(min_length=1, max_length=MAX_CHAT_CONTENT_LENGTH)
    # Optional multimodal attachments (OpenAI vision form): data URLs. Only
    # honoured in plain-chat conversations; RAG grounding stays text-only.
    images: Optional[list[str]] = None

    @field_validator("content")
    @classmethod
    def _strip_content(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("content must not be blank")
        return stripped

    @field_validator("images")
    @classmethod
    def _check_images(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if not value:
            return None
        if len(value) > MAX_CHAT_IMAGES:
            raise ValueError(f"最多附带 {MAX_CHAT_IMAGES} 张图片")
        for image in value:
            if not image.startswith("data:image/"):
                raise ValueError("图片必须以 data:image/ 开头（base64 data URL）")
            if len(image) > MAX_CHAT_IMAGE_CHARS:
                raise ValueError("单张图片超过 5 MB 限制")
        return value
