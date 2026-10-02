"""Auth service — registration, login, identity resolution."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.repositories.user_repo import UserRepository
from app.schemas.auth import LoginRequest, RegisterRequest, TokenWithUser
from app.schemas.user import UserCreate, UserRead
from app.core.config import settings


class AuthService:
    def __init__(self, db: Session) -> None:
        self.repo = UserRepository(db)

    # ---------- registration ----------
    def register(self, payload: RegisterRequest) -> UserRead:
        if self.repo.get_by_username(payload.username):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Username already taken",
            )
        if self.repo.get_by_email(payload.email):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already registered",
            )

        user = self.repo.create(
            username=payload.username,
            email=payload.email,
            hashed_password=hash_password(payload.password),
        )
        return UserRead.model_validate(user)

    # ---------- login ----------
    def login(self, payload: LoginRequest) -> TokenWithUser:
        user = self.repo.get_by_username_or_email(payload.username)
        if not user or not verify_password(payload.password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
            )
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is disabled",
            )

        token = create_access_token(subject=user.id)
        return TokenWithUser(
            access_token=token,
            token_type="bearer",
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user=UserRead.model_validate(user),
        )