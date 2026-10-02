"""User-related Pydantic schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import EmailStr, Field

from app.schemas.common import ORMModel


class UserBase(ORMModel):
    username: str = Field(min_length=3, max_length=64)
    email: EmailStr


class UserCreate(UserBase):
    password: str = Field(min_length=6, max_length=128)


class UserUpdate(ORMModel):
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=6, max_length=128)


class UserRead(UserBase):
    id: int
    is_active: bool
    is_superuser: bool
    created_at: datetime