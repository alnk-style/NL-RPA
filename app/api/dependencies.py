from __future__ import annotations

from typing import Any

from fastapi import (
    Depends,
    HTTPException,
    Request,
    WebSocket,
    status,
)
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User

DEV_USER_USERNAME = "local-dev"
DEV_USER_EMAIL = "local-dev@example.com"


def _jwt_secret() -> str:
    secret = getattr(settings, "JWT_SECRET_KEY", None)
    if secret:
        return str(secret)

    secret = getattr(settings, "SECRET_KEY", None)
    if secret:
        return str(secret)

    return settings.CREDENTIAL_ENCRYPTION_KEY


def _jwt_algorithm() -> str:
    return str(
        getattr(
            settings,
            "JWT_ALGORITHM",
            getattr(settings, "ALGORITHM", "HS256"),
        )
    )


def _credentials_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


SECRET_KEY = _jwt_secret()
ALGORITHM = _jwt_algorithm()


async def ensure_dev_user(db: AsyncSession) -> User:
    result = await db.execute(
        select(User).where(User.username == DEV_USER_USERNAME)
    )
    user = result.scalar_one_or_none()
    if user is not None:
        return user

    user = User(
        username=DEV_USER_USERNAME,
        email=DEV_USER_EMAIL,
        hashed_password="dev-auth-disabled",
    )
    db.add(user)
    try:
        await db.commit()
        await db.refresh(user)
        return user
    except IntegrityError:
        await db.rollback()
        result = await db.execute(
            select(User).where(User.username == DEV_USER_USERNAME)
        )
        user = result.scalar_one_or_none()
        if user is None:
            raise
        return user


async def get_current_user(
    token: str | None = None,
    request: Request = None,
    websocket: WebSocket = None,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the authenticated user from a bearer JWT."""

    if token is None and request is not None:
        authorization = request.headers.get("Authorization", "")
        scheme, _, credentials = authorization.partition(" ")
        if scheme.lower() == "bearer" and credentials:
            token = credentials
    if token is None and websocket is not None:
        token = websocket.query_params.get("token")

    if token is None:
        if settings.DEV_AUTH_BYPASS:
            return await ensure_dev_user(db)
        raise _credentials_exception()

    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
        )
    except JWTError as exc:
        raise _credentials_exception() from exc

    subject = payload.get("sub")
    if subject is None:
        raise _credentials_exception()

    user: User | None = None
    try:
        user_id = int(subject)
    except (TypeError, ValueError):
        user_id = None

    if user_id is not None:
        user = await db.get(User, user_id)

    if user is None and isinstance(subject, str):
        result = await db.execute(
            select(User).where(User.username == subject)
        )
        user = result.scalar_one_or_none()

    if user is None:
        raise _credentials_exception()

    return user
