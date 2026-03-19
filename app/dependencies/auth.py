"""FastAPI dependencies for authentication and CSRF verification."""
import secrets
import uuid

import jwt
from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.user import User


def _extract_bearer_token(authorization: str | None, access_token: str | None) -> str | None:
    if authorization and authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ")
    return access_token


async def _resolve_user_from_token(token: str | None, db: AsyncSession) -> User | None:
    if not token:
        return None
    try:
        payload = decode_access_token(token)
        user_id_str: str = payload["sub"]
    except (jwt.PyJWTError, KeyError):
        return None
    return await db.get(User, uuid.UUID(user_id_str))


async def get_current_user(
    authorization: str | None = Header(default=None),
    access_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Extract and verify the Bearer access token; return the authenticated user."""
    token = _extract_bearer_token(authorization, access_token)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await _resolve_user_from_token(token, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_optional_current_user(
    authorization: str | None = Header(default=None),
    access_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Return the authenticated user when available, otherwise ``None``."""
    token = _extract_bearer_token(authorization, access_token)
    return await _resolve_user_from_token(token, db)


def require_csrf(
    x_csrf_token: str | None = Header(default=None, alias="x-csrf-token"),
    csrf_token: str | None = Cookie(default=None),
) -> None:
    """
    Double-submit cookie CSRF check.
    Used on cookie-based endpoints (refresh, logout) where the refresh token
    is sent automatically and CSRF protection is therefore required.
    """
    if not x_csrf_token or not csrf_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token missing")
    if not secrets.compare_digest(x_csrf_token, csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token invalid")
