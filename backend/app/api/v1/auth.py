"""Auth router: register, login, /me."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DBSession
from app.schemas.auth import LoginRequest, RegisterRequest, TokenWithUser
from app.schemas.common import Message
from app.schemas.user import UserRead
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
def register(payload: RegisterRequest, db: DBSession) -> UserRead:
    return AuthService(db).register(payload)


@router.post(
    "/login",
    response_model=TokenWithUser,
    summary="Login with username or email and obtain a JWT",
)
def login(payload: LoginRequest, db: DBSession) -> TokenWithUser:
    return AuthService(db).login(payload)


@router.get(
    "/me",
    response_model=UserRead,
    summary="Return the currently authenticated user",
)
def me(current_user: CurrentUser) -> UserRead:
    return UserRead.model_validate(current_user)


@router.post(
    "/logout",
    response_model=Message,
    summary="Logout placeholder (JWT is stateless; client drops the token)",
)
def logout(_: CurrentUser) -> Message:
    # Real revocation lists (Redis-backed) can be added later.
    return Message(detail="Logged out")