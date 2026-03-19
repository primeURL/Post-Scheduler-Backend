"""Authentication routes: Google OAuth 2.0 + Firebase ID-token flow."""
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.firebase import verify_id_token
from app.core.limiter import limiter
from app.core.security import (
    create_access_token,
    generate_csrf_token,
    generate_refresh_token,
    hash_token,
    tokens_match,
)
from app.dependencies.auth import get_current_user, require_csrf
from app.models.user import User
from app.models.user_session import UserSession
from app.schemas.auth import TokenResponse, UserRead
from app.services import google_oauth

router = APIRouter(prefix="/auth", tags=["auth"])

_REFRESH_COOKIE = "refresh_token"
_CSRF_COOKIE = "csrf_token"
_COOKIE_SECURE = settings.app_env == "production"


class _FirebaseLoginRequest(BaseModel):
    id_token: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_auth_cookies(response: Response, refresh_token: str, csrf_token: str) -> None:
    max_age = settings.jwt_refresh_expire_days * 86400
    response.set_cookie(
        _REFRESH_COOKIE,
        refresh_token,
        max_age=max_age,
        httponly=True,
        secure=_COOKIE_SECURE,
        samesite="lax",
    )
    response.set_cookie(
        _CSRF_COOKIE,
        csrf_token,
        max_age=max_age,
        httponly=False,  # JS-readable so the client can pass it in X-CSRF-Token
        secure=_COOKIE_SECURE,
        samesite="lax",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(_REFRESH_COOKIE)
    response.delete_cookie(_CSRF_COOKIE)


async def _upsert_user(db: AsyncSession, *, google_sub: str, email: str, name: str, avatar_url: str | None) -> User:
    result = await db.execute(select(User).where(User.google_sub == google_sub))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(google_sub=google_sub, email=email, name=name, avatar_url=avatar_url)
        db.add(user)
    else:
        user.email = email
        user.name = name
        user.avatar_url = avatar_url
    await db.flush()
    return user


async def _create_session(db: AsyncSession, user: User, refresh_token: str) -> None:
    session = UserSession(
        user_id=user.id,
        refresh_token_hash=hash_token(refresh_token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_expire_days),
    )
    db.add(session)
    await db.commit()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/google")
@limiter.limit("20/minute")
async def google_login(request: Request) -> RedirectResponse:
    """Redirect the browser to Google's OAuth consent screen."""
    state = secrets.token_urlsafe(32)
    redirect_url = google_oauth.build_authorization_url(state)
    response = RedirectResponse(url=redirect_url)
    response.set_cookie(
        "oauth_state",
        state,
        max_age=600,
        httponly=True,
        secure=_COOKIE_SECURE,
        samesite="lax",
    )
    return response


@router.get("/google/callback")
@limiter.limit("20/minute")
async def google_callback(
    request: Request,
    code: str,
    state: str,
    oauth_state: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Exchange Google authorization code for tokens, upsert user, issue session."""
    # CSRF — verify state matches what was stored in the cookie
    if not oauth_state or not secrets.compare_digest(state, oauth_state):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth state")

    # Exchange code → Google tokens
    try:
        google_tokens = await google_oauth.exchange_code(code)
        user_info = await google_oauth.get_user_info(google_tokens["access_token"])
    except httpx.HTTPStatusError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Google OAuth failed")

    google_sub: str = user_info["sub"]
    email: str = user_info["email"]
    name: str = user_info.get("name", email)
    avatar_url: str | None = user_info.get("picture")

    user = await _upsert_user(db, google_sub=google_sub, email=email, name=name, avatar_url=avatar_url)

    refresh_token = generate_refresh_token()
    csrf_token = generate_csrf_token()
    await _create_session(db, user, refresh_token)

    access_token = create_access_token(user.id, user.email)

    redirect = RedirectResponse(url=f"{settings.frontend_url}/dashboard")
    # Access token in a short-lived JS-readable cookie so the frontend can
    # pick it up, store in memory, and clear the cookie.
    redirect.set_cookie(
        "access_token",
        access_token,
        max_age=settings.jwt_access_expire_minutes * 60,
        httponly=False,
        secure=_COOKIE_SECURE,
        samesite="lax",
    )
    _set_auth_cookies(redirect, refresh_token, csrf_token)
    redirect.delete_cookie("oauth_state")
    return redirect


@router.post("/firebase", response_model=TokenResponse)
@limiter.limit("20/minute")
async def firebase_login(
    request: Request,
    response: Response,
    body: _FirebaseLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Verify a Firebase ID token, upsert the user, and issue a session."""
    try:
        claims = await verify_id_token(body.id_token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Firebase token")

    google_sub: str = claims["uid"]
    email: str = claims.get("email", "")
    name: str = claims.get("name", email)
    avatar_url: str | None = claims.get("picture")

    user = await _upsert_user(db, google_sub=google_sub, email=email, name=name, avatar_url=avatar_url)

    refresh_token = generate_refresh_token()
    csrf_token = generate_csrf_token()
    await _create_session(db, user, refresh_token)

    access_token = create_access_token(user.id, user.email)
    _set_auth_cookies(response, refresh_token, csrf_token)

    expire_seconds = settings.jwt_access_expire_minutes * 60
    return {"access_token": access_token, "token_type": "bearer", "expires_in": expire_seconds}


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("30/minute")
async def refresh_token(
    request: Request,
    _csrf: None = Depends(require_csrf),
    refresh_token: str | None = Cookie(default=None, alias=_REFRESH_COOKIE),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Issue a new access token using the httpOnly refresh token cookie."""
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No refresh token")

    token_hash = hash_token(refresh_token)
    result = await db.execute(
        select(UserSession).where(
            UserSession.refresh_token_hash == token_hash,
            UserSession.expires_at > datetime.now(timezone.utc),
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    user = await db.get(User, session.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    access_token = create_access_token(user.id, user.email)
    expire_seconds = settings.jwt_access_expire_minutes * 60
    return {"access_token": access_token, "token_type": "bearer", "expires_in": expire_seconds}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("30/minute")
async def logout(
    request: Request,
    response: Response,
    _csrf: None = Depends(require_csrf),
    refresh_token: str | None = Cookie(default=None, alias=_REFRESH_COOKIE),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Invalidate the current session and clear auth cookies."""
    if refresh_token:
        token_hash = hash_token(refresh_token)
        result = await db.execute(
            select(UserSession).where(UserSession.refresh_token_hash == token_hash)
        )
        session = result.scalar_one_or_none()
        if session:
            await db.delete(session)
            await db.commit()

    _clear_auth_cookies(response)


@router.get("/me", response_model=UserRead)
async def get_me(current_user: User = Depends(get_current_user)) -> User:
    """Return the authenticated user's profile."""
    return current_user
