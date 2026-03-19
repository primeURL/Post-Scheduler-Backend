"""Connected social accounts — list, disconnect, and X OAuth 2.0 connect flow."""
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.limiter import limiter
from app.core.redis import get_redis
from app.core.security import encrypt_token
from app.dependencies.auth import get_current_user, get_optional_current_user
from app.models.connected_account import ConnectedAccount
from app.models.user import User
from app.schemas.account import ConnectedAccountRead, XConnectResponse
from app.services import x_oauth

router = APIRouter(prefix="/accounts", tags=["accounts"])

_PKCE_TTL = 600  # 10 minutes
_DEV_USER_EMAIL = "dev@local.post-scheduler"
_DEV_USER_GOOGLE_SUB = "dev-local-user"
_pkce_fallback_store: dict[str, tuple[datetime, str]] = {}


@router.get("", response_model=list[ConnectedAccountRead])
async def list_accounts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ConnectedAccount]:
    """Return all social accounts connected to the current user."""
    result = await db.execute(
        select(ConnectedAccount).where(ConnectedAccount.user_id == current_user.id)
    )
    return list(result.scalars().all())


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_account(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Disconnect a social account. Only the owning user may delete it."""
    account = await db.get(ConnectedAccount, account_id)
    if not account or account.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    await db.delete(account)
    await db.commit()


# ---------------------------------------------------------------------------
# X (Twitter) OAuth 2.0 PKCE flow
# ---------------------------------------------------------------------------


@router.get("/x/connect", response_model=XConnectResponse)
# @limiter.limit("20/minute")
async def x_connect(
    request: Request,
    next: str | None = Query(default=None),
    current_user: User | None = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_db),
) -> XConnectResponse:
    """Return the X OAuth 2.0 authorization URL.

    The frontend redirects the browser to ``authorization_url``.  The PKCE
    ``code_verifier`` and ``user_id`` are stored in Redis under ``state`` so
    the callback can retrieve them.
    """
    resolved_user = await _resolve_x_connect_user(current_user, db)

    redis = get_redis()
    code_verifier, code_challenge = x_oauth.generate_pkce_pair()
    state = secrets.token_urlsafe(32)
    payload = json.dumps(
        {
            "code_verifier": code_verifier,
            "user_id": str(resolved_user.id),
            "frontend_url": _resolve_frontend_origin(request),
            "next_path": _resolve_next_path(next),
        }
    )

    try:
        await redis.setex(f"x_oauth:{state}", _PKCE_TTL, payload)
    except Exception:
        if settings.app_env == "production":
            raise
        _store_pkce_fallback(state, payload)

    return XConnectResponse(
        authorization_url=x_oauth.build_authorization_url(state, code_challenge)
    )


async def _resolve_x_connect_user(current_user: User | None, db: AsyncSession) -> User:
    """Resolve the owning app user for X OAuth.

    In development, allow the X connect flow before Google auth exists by
    auto-provisioning a single local user. Production remains strict.
    """
    if current_user:
        return current_user

    if settings.app_env == "production":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticate before connecting an X account",
        )

    result = await db.execute(select(User).where(User.google_sub == _DEV_USER_GOOGLE_SUB))
    user = result.scalar_one_or_none()
    if user:
        return user

    user = User(
        email=_DEV_USER_EMAIL,
        name="Local Dev User",
        google_sub=_DEV_USER_GOOGLE_SUB,
        avatar_url=None,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/x/callback")
@limiter.limit("20/minute")
async def x_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Handle the redirect from X after the user grants access.

    On success, redirects to ``{frontend_url}/settings/accounts?connected=x``.
    On any failure, redirects with an ``error`` query param.
    """
    redis = get_redis()
    redis_key = f"x_oauth:{state}"

    try:
        raw = await redis.get(redis_key)
        if raw:
            await redis.delete(redis_key)
    except Exception:
        if settings.app_env == "production":
            raw = None
        else:
            raw = _pop_pkce_fallback(state)
    if not raw:
        return RedirectResponse(
            _build_frontend_redirect(settings.frontend_url, "/settings/accounts", "error=x_auth_expired"),
            status_code=status.HTTP_302_FOUND,
        )

    data = json.loads(raw)
    code_verifier: str = data["code_verifier"]
    user_id = uuid.UUID(data["user_id"])
    frontend_url = data.get("frontend_url") or settings.frontend_url
    next_path = _resolve_next_path(data.get("next_path"))

    try:
        token_data = await x_oauth.exchange_code(code, code_verifier)
    except Exception:
        return RedirectResponse(
            _build_frontend_redirect(frontend_url, next_path, "error=x_token_exchange_failed"),
            status_code=status.HTTP_302_FOUND,
        )

    try:
        user_info = await x_oauth.get_user_info(token_data["access_token"])
    except Exception:
        return RedirectResponse(
            _build_frontend_redirect(frontend_url, next_path, "error=x_userinfo_failed"),
            status_code=status.HTTP_302_FOUND,
        )

    access_token_enc = encrypt_token(token_data["access_token"])
    refresh_token_enc = (
        encrypt_token(token_data["refresh_token"])
        if token_data.get("refresh_token")
        else None
    )
    token_expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=token_data["expires_in"])
        if token_data.get("expires_in")
        else None
    )

    result = await db.execute(
        select(ConnectedAccount).where(
            ConnectedAccount.user_id == user_id,
            ConnectedAccount.platform == "x",
        )
    )
    account = result.scalar_one_or_none()

    if account:
        account.platform_user_id = user_info["id"]
        account.platform_username = user_info["username"]
        account.access_token_enc = access_token_enc
        account.refresh_token_enc = refresh_token_enc
        account.token_expires_at = token_expires_at
        account.scopes = token_data.get("scope")
        account.subscription_type = user_info.get("subscription_type")
        account.avatar_url = user_info.get("profile_image_url")
    else:
        account = ConnectedAccount(
            user_id=user_id,
            platform="x",
            platform_user_id=user_info["id"],
            platform_username=user_info["username"],
            access_token_enc=access_token_enc,
            refresh_token_enc=refresh_token_enc,
            token_expires_at=token_expires_at,
            scopes=token_data.get("scope"),
            subscription_type=user_info.get("subscription_type"),
            avatar_url=user_info.get("profile_image_url"),
        )
        db.add(account)

    await db.commit()

    return RedirectResponse(
        _build_frontend_redirect(frontend_url, next_path, "connected=x"),
        status_code=status.HTTP_302_FOUND,
    )


def _resolve_frontend_origin(request: Request) -> str:
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")

    for candidate in (origin, referer, settings.frontend_url):
        normalized = _normalize_origin(candidate)
        if normalized:
            return normalized

    return settings.frontend_url


def _normalize_origin(url: str | None) -> str | None:
    if not url:
        return None

    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return None

    return f"{parsed.scheme}://{parsed.netloc}"


def _resolve_next_path(path: str | None) -> str:
    if not path or not path.startswith("/"):
        return "/settings/accounts"

    if path.startswith("//"):
        return "/settings/accounts"

    return path


def _build_frontend_redirect(frontend_url: str, next_path: str, query: str) -> str:
    origin = _normalize_origin(frontend_url) or settings.frontend_url
    return f"{origin}{next_path}?{query}"


def _store_pkce_fallback(state: str, payload: str) -> None:
    _prune_pkce_fallback()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=_PKCE_TTL)
    _pkce_fallback_store[state] = (expires_at, payload)


def _pop_pkce_fallback(state: str) -> str | None:
    _prune_pkce_fallback()
    entry = _pkce_fallback_store.pop(state, None)
    if not entry:
        return None
    expires_at, payload = entry
    if expires_at <= datetime.now(timezone.utc):
        return None
    return payload


def _prune_pkce_fallback() -> None:
    now = datetime.now(timezone.utc)
    expired_states = [
        state for state, (expires_at, _) in _pkce_fallback_store.items() if expires_at <= now
    ]
    for state in expired_states:
        _pkce_fallback_store.pop(state, None)
