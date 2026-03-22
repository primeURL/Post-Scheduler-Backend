"""Analytics routes — read-only access to post performance metrics."""
import time
import uuid
from collections import deque

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from redis.exceptions import RedisError
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.limiter import limiter
from app.core.redis import get_redis
from app.core.security import decrypt_token
from app.dependencies.auth import get_current_user
from app.models.connected_account import ConnectedAccount
from app.models.post import Post
from app.models.post_analytics import PostAnalytics
from app.models.user import User
from app.schemas.analytics import PostAnalyticsLatestRead, PostAnalyticsRead
from app.services.x_api import XApiService

router = APIRouter(prefix="/analytics", tags=["analytics"])

_MANUAL_REFRESH_POST_COOLDOWN_SECONDS = 300
_MANUAL_REFRESH_USER_WINDOW_SECONDS = 600
_MANUAL_REFRESH_USER_LIMIT = 12

_fallback_post_cooldowns: dict[str, float] = {}
_fallback_user_hits: dict[str, deque[float]] = {}


def _raise_mapped_x_error(exc: httpx.HTTPStatusError) -> None:
    """Map upstream X API failures to client-safe HTTP errors."""
    if exc.response.status_code == status.HTTP_401_UNAUTHORIZED:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X authorization failed. Reconnect your X account and try again.",
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(exc),
    )


@router.get("/posts", response_model=list[PostAnalyticsLatestRead])
async def list_posts_analytics(
    include_deleted: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[PostAnalyticsLatestRead]:
    """Return all user posts with the latest available analytics snapshot."""
    latest_subq = (
        select(
            PostAnalytics.post_id.label("post_id"),
            func.max(PostAnalytics.fetched_at).label("max_fetched_at"),
        )
        .group_by(PostAnalytics.post_id)
        .subquery()
    )

    filters = [Post.user_id == current_user.id]
    if not include_deleted:
        filters.append(Post.is_deleted.is_(False))

    result = await db.execute(
        select(Post, PostAnalytics)
        .outerjoin(latest_subq, latest_subq.c.post_id == Post.id)
        .outerjoin(
            PostAnalytics,
            and_(
                PostAnalytics.post_id == Post.id,
                PostAnalytics.fetched_at == latest_subq.c.max_fetched_at,
            ),
        )
        .where(*filters)
        .order_by(func.coalesce(Post.published_at, Post.created_at).desc())
    )

    rows = result.all()

    platform_post_ids = [
        post.platform_post_id
        for post, _ in rows
        if post.platform_post_id
    ]
    quote_action_ids: set[str] = set()
    if platform_post_ids:
        quote_rows = await db.execute(
            select(Post.quote_of_platform_post_id)
            .where(
                Post.user_id == current_user.id,
                Post.quote_of_platform_post_id.in_(platform_post_ids),
                Post.is_deleted.is_(False),
            )
            .distinct()
        )
        quote_action_ids = {
            quote_of_id for quote_of_id in quote_rows.scalars().all() if quote_of_id
        }

    response: list[PostAnalyticsLatestRead] = []
    for post, snapshot in rows:
        response.append(
            PostAnalyticsLatestRead(
                post_id=post.id,
                x_post_id=post.platform_post_id,
                content=post.content,
                status=post.status.value,
                is_deleted=post.is_deleted,
                scheduled_for=post.scheduled_for,
                published_at=post.published_at,
                fetched_at=snapshot.fetched_at if snapshot else None,
                impression_count=snapshot.impressions if snapshot else 0,
                like_count=snapshot.likes if snapshot else 0,
                repost_count=snapshot.retweets if snapshot else 0,
                reply_count=snapshot.replies if snapshot else 0,
                quoted_count=snapshot.quoted_count if snapshot else 0,
                bookmarks=snapshot.bookmarks if snapshot else 0,
                has_repost_action=post.reposted_at is not None,
                has_quote_action=(post.platform_post_id in quote_action_ids) if post.platform_post_id else False,
            )
        )
    return response


@router.get("/posts/{post_id}", response_model=list[PostAnalyticsRead])
async def get_post_analytics(
    post_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[PostAnalytics]:
    """Return all analytics snapshots for a post owned by the current user."""
    post = await db.get(Post, post_id)
    if not post or post.user_id != current_user.id or post.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    result = await db.execute(
        select(PostAnalytics)
        .where(PostAnalytics.post_id == post_id)
        .order_by(PostAnalytics.fetched_at.asc())
    )
    return list(result.scalars().all())


@router.post("/posts/{post_id}/refresh", response_model=PostAnalyticsLatestRead)
@limiter.limit("10/minute")
async def refresh_post_analytics(
    request: Request,
    post_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PostAnalyticsLatestRead:
    """Fetch latest metrics from X API for one post and persist snapshot.

    Guardrails:
    - HTTP-level rate limit via SlowAPI decorator
    - Redis per-user budget window
    - Redis per-user-per-post cooldown lock
    """
    post = await db.get(Post, post_id)
    if not post or post.user_id != current_user.id or post.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    if post.status.value != "published" or not post.platform_post_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only published posts with x_post_id can be refreshed",
        )
    if not post.connected_account_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Connected account not found")

    account = await db.get(ConnectedAccount, post.connected_account_id)
    if not account:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Connected account not found")

    await _enforce_manual_refresh_limits(str(current_user.id), str(post.id))

    access_token = decrypt_token(account.access_token_enc)
    api = XApiService(access_token=access_token)
    try:
        metrics = await api.get_tweet_metrics(post.platform_post_id)
    except httpx.HTTPStatusError as exc:
        _raise_mapped_x_error(exc)

    snapshot = PostAnalytics(
        post_id=post.id,
        impressions=metrics["impressions"],
        likes=metrics["likes"],
        retweets=metrics["retweets"],
        replies=metrics["replies"],
        quoted_count=metrics["quoted_count"],
        bookmarks=metrics["bookmarks"],
        clicks=metrics["clicks"],
        profile_visits=metrics["profile_visits"],
    )
    db.add(snapshot)
    await db.commit()
    await db.refresh(snapshot)

    return PostAnalyticsLatestRead(
        post_id=post.id,
        x_post_id=post.platform_post_id,
        content=post.content,
        status=post.status.value,
        is_deleted=post.is_deleted,
        scheduled_for=post.scheduled_for,
        published_at=post.published_at,
        fetched_at=snapshot.fetched_at,
        impression_count=snapshot.impressions,
        like_count=snapshot.likes,
        repost_count=snapshot.retweets,
        reply_count=snapshot.replies,
        quoted_count=snapshot.quoted_count,
        bookmarks=snapshot.bookmarks,
        has_repost_action=post.reposted_at is not None,
        has_quote_action=False,
    )


async def _enforce_manual_refresh_limits(user_id: str, post_id: str) -> None:
    """Apply strict cooldown+budget limits for manual refresh.

    Uses Redis when available; falls back to process-local memory when Redis
    is unavailable so the endpoint never fails with 500 in local dev.
    """
    try:
        await _enforce_limits_redis(user_id, post_id)
    except (RedisError, ConnectionError, RuntimeError):
        _enforce_limits_fallback(user_id, post_id)


async def _enforce_limits_redis(user_id: str, post_id: str) -> None:
    redis = get_redis()
    lock_key = f"analytics:manual:post:{user_id}:{post_id}"
    got_lock = await redis.set(lock_key, "1", ex=_MANUAL_REFRESH_POST_COOLDOWN_SECONDS, nx=True)
    if not got_lock:
        ttl = await redis.ttl(lock_key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Manual refresh cooldown active for this post. "
                f"Try again in {max(ttl, 1)} seconds."
            ),
        )

    budget_key = f"analytics:manual:user:{user_id}:budget"
    user_hits = await redis.incr(budget_key)
    if user_hits == 1:
        await redis.expire(budget_key, _MANUAL_REFRESH_USER_WINDOW_SECONDS)
    if user_hits > _MANUAL_REFRESH_USER_LIMIT:
        ttl = await redis.ttl(budget_key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Manual refresh rate limit exceeded. "
                f"Try again in {max(ttl, 1)} seconds."
            ),
        )


def _enforce_limits_fallback(user_id: str, post_id: str) -> None:
    now = time.time()
    lock_key = f"analytics:manual:post:{user_id}:{post_id}"
    lock_expires_at = _fallback_post_cooldowns.get(lock_key, 0)
    if lock_expires_at > now:
        retry_in = int(lock_expires_at - now)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Manual refresh cooldown active for this post. "
                f"Try again in {max(retry_in, 1)} seconds."
            ),
        )
    _fallback_post_cooldowns[lock_key] = now + _MANUAL_REFRESH_POST_COOLDOWN_SECONDS

    budget_key = f"analytics:manual:user:{user_id}:budget"
    q = _fallback_user_hits.get(budget_key)
    if q is None:
        q = deque()
        _fallback_user_hits[budget_key] = q

    window_start = now - _MANUAL_REFRESH_USER_WINDOW_SECONDS
    while q and q[0] < window_start:
        q.popleft()

    if len(q) >= _MANUAL_REFRESH_USER_LIMIT:
        retry_in = int(_MANUAL_REFRESH_USER_WINDOW_SECONDS - (now - q[0]))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Manual refresh rate limit exceeded. "
                f"Try again in {max(retry_in, 1)} seconds."
            ),
        )

    q.append(now)
