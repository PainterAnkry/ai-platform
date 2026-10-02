"""Authentication-related Pydantic schemas."""

from __future__ import annotations

from pydantic import Field

from app.schemas.common import ORMModel
from app.schemas.user import UserRead


class LoginRequest(ORMModel):
    username: str = Field(description="Username or email address")
    password: str


class RegisterRequest(ORMModel):
    username: str = Field(min_length=3, max_length=64)
    email: str = Field(description="User email address")
    password: str = Field(min_length=6, max_length=128)


class Token(ORMModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Token lifetime in seconds")


class TokenWithUser(Token):
    user: UserRead