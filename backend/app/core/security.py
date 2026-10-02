"""Password hashing + JWT helpers.

Uses the ``bcrypt`` library directly rather than ``passlib``:
``passlib`` 1.7.4 is unmaintained and breaks against ``bcrypt >= 4.1``
(it reads the removed ``bcrypt.__about__`` and raises on the 72-byte check).

Because bcrypt silently truncates at 72 bytes, passwords are pre-hashed with
SHA-256 and base64-encoded first. That removes the input limit entirely while
preserving the full password entropy.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import bcrypt
import jwt

from app.core.config import settings

# bcrypt cost factor. 12 is a good default balance for an interactive app.
BCRYPT_ROUNDS = 12


# ---------- Password ----------
def _prepare_password(plain: str) -> bytes:
    """Pre-hash a password into a fixed-size, 72-byte-safe bcrypt input."""
    digest = hashlib.sha256(plain.encode("utf-8")).digest()
    return base64.b64encode(digest)  # 44 bytes


def hash_password(plain: str) -> str:
    """Return a bcrypt hash for ``plain``."""
    hashed = bcrypt.hashpw(
        _prepare_password(plain),
        bcrypt.gensalt(rounds=BCRYPT_ROUNDS),
    )
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if ``plain`` matches ``hashed``."""
    try:
        return bcrypt.checkpw(_prepare_password(plain), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed hash in the database — treat as a failed match.
        return False


# ---------- JWT ----------
def _create_token(
    subject: str | int,
    expires_delta: timedelta,
    token_type: str,
    extra_claims: Optional[Dict[str, Any]] = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "type": token_type,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(
    subject: str | int,
    expires_delta: Optional[timedelta] = None,
    extra_claims: Optional[Dict[str, Any]] = None,
) -> str:
    delta = expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return _create_token(subject, delta, token_type="access", extra_claims=extra_claims)


def decode_token(token: str) -> Dict[str, Any]:
    """Decode a JWT and return its payload. Raises ``jwt.PyJWTError`` on failure."""
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])