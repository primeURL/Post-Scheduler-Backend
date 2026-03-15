"""Connected social accounts — list, disconnect, and X OAuth 2.0 connect flow."""
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.limiter import limiter
from app.core.redis import get_redis
from app.core.security import encrypt_token
from app.dependencies.auth import get_current_user
from app.models.connected_account import ConnectedAccount
from app.models.user import User
from app.schemas.account import ConnectedAccountRead, XConnectResponse
from app.services import x_oauth

router = APIRouter(prefix="/accounts", tags=["accounts"])

_PKCE_TTL = 600  # 10 minutes


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
@limiter.limit("20/minute")
async def x_connect(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> XConnectResponse:
    """Return the X OAuth 2.0 authorization URL.

    The frontend redirects the browser to ``authorization_url``.  The PKCE
    ``code_verifier`` and ``user_id`` are stored in Redis under ``state`` so
    the callback can retrieve them.
    """
    redis = get_redis()
    code_verifier, code_challenge = x_oauth.generate_pkce_pair()
    state = secrets.token_urlsafe(32)

    await redis.setex(
        f"x_oauth:{state}",
        _PKCE_TTL,
        json.dumps({"code_verifier": code_verifier, "user_id": str(current_user.id)}),
    )

    return XConnectResponse(
        authorization_url=x_oauth.build_authorization_url(state, code_challenge)
    )


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

    raw = await redis.get(redis_key)
    if not raw:
        return RedirectResponse(
            f"{settings.frontend_url}/settings/accounts?error=x_auth_expired",
            status_code=status.HTTP_302_FOUND,
        )
    await redis.delete(redis_key)

    data = json.loads(raw)
    code_verifier: str = data["code_verifier"]
    user_id = uuid.UUID(data["user_id"])

    try:
        token_data = await x_oauth.exchange_code(code, code_verifier)
    except Exception:
        return RedirectResponse(
            f"{settings.frontend_url}/settings/accounts?error=x_token_exchange_failed",
            status_code=status.HTTP_302_FOUND,
        )

    try:
        user_info = await x_oauth.get_user_info(token_data["access_token"])
    except Exception:
        return RedirectResponse(
            f"{settings.frontend_url}/settings/accounts?error=x_userinfo_failed",
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
        )
        db.add(account)

    await db.commit()

    return RedirectResponse(
        f"{settings.frontend_url}/settings/accounts?connected=x",
        status_code=status.HTTP_302_FOUND,
    )
