"""User repository — pure DB access, no business logic."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User


class UserRepository:
    """Encapsulates all SQL access for the ``users`` table."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ---------- lookups ----------
    def get_by_id(self, user_id: int) -> Optional[User]:
        return self.db.get(User, user_id)

    def get_by_username(self, username: str) -> Optional[User]:
        stmt = select(User).where(User.username == username)
        return self.db.execute(stmt).scalar_one_or_none()

    def get_by_email(self, email: str) -> Optional[User]:
        stmt = select(User).where(User.email == email)
        return self.db.execute(stmt).scalar_one_or_none()

    def get_by_username_or_email(self, identifier: str) -> Optional[User]:
        """Allow login by either username or email."""
        stmt = select(User).where((User.username == identifier) | (User.email == identifier))
        return self.db.execute(stmt).scalar_one_or_none()

    # ---------- mutations ----------
    def create(
        self,
        *,
        username: str,
        email: str,
        hashed_password: str,
        is_superuser: bool = False,
    ) -> User:
        user = User(
            username=username,
            email=email,
            hashed_password=hashed_password,
            is_superuser=is_superuser,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user