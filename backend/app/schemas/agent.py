"""Agent / tool / data-file Pydantic schemas (Phase 5)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import Field, field_validator

from app.schemas.common import ORMModel

ALLOWED_DATA_TYPES: frozenset[str] = frozenset({".xlsx", ".xls", ".csv"})


class AgentCreate(ORMModel):
    name: str = Field(min_length=1, max_length=128)
    system_prompt: str = Field(default="", max_length=8000)
    # Optional per-agent model override (provider-specific name); empty = global.
    model: Optional[str] = Field(default=None, max_length=128)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    # Enabled tool names; None/empty = all builtin tools.
    tools: Optional[list[str]] = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped


class AgentUpdate(ORMModel):
    """Partial update — only supplied fields are applied."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    system_prompt: Optional[str] = Field(default=None, max_length=8000)
    model: Optional[str] = Field(default=None, max_length=128)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    tools: Optional[list[str]] = None


class AgentRead(ORMModel):
    id: int
    name: str
    system_prompt: str
    model: Optional[str] = None
    temperature: Optional[float] = None
    tools: Optional[list[str]] = None
    created_at: datetime
    updated_at: datetime


class ToolRead(ORMModel):
    id: int
    name: str
    description: str
    type: str
    enabled: bool
    config: Optional[dict[str, Any]] = None


class DataFileRead(ORMModel):
    id: int
    filename: str
    file_type: str
    file_size: int
    row_count: int
    column_names: Optional[list[str]] = None
    created_at: datetime
