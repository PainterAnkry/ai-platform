"""Reusable Pydantic base models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    """Base Pydantic model that allows reading attributes from ORM objects."""

    model_config = ConfigDict(from_attributes=True)


class Message(BaseModel):
    detail: str