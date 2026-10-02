"""Unit tests for security helpers."""

from __future__ import annotations

import time

import jwt
import pytest

from app.core.config import settings
from app.core.security import (
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_password_hash_and_verify():
    h = hash_password("hunter2-correct-horse")
    assert h != "hunter2-correct-horse"
    assert verify_password("hunter2-correct-horse", h)
    assert not verify_password("wrong", h)


def test_jwt_roundtrip():
    token = create_access_token(subject=42)
    payload = decode_token(token)
    assert payload["sub"] == "42"
    assert payload["type"] == "access"


def test_decode_invalid_token_raises():
    with pytest.raises(jwt.PyJWTError):
        decode_token("not-a-real-token")


def test_settings_exposes_database_url():
    """The DSN honours DATABASE_URL, else it is built from POSTGRES_*."""
    if settings.DATABASE_URL.strip():
        assert settings.database_url == settings.DATABASE_URL.strip()
    else:
        assert "postgresql+psycopg2://" in settings.database_url


def test_long_password_is_not_truncated():
    """bcrypt truncates at 72 bytes; the SHA-256 pre-hash prevents that."""
    long_password = "x" * 200
    hashed = hash_password(long_password)
    assert verify_password(long_password, hashed)
    assert not verify_password("x" * 199 + "y", hashed)


def test_verify_rejects_malformed_hash():
    assert not verify_password("anything", "not-a-bcrypt-hash")