"""Background jobs: publish scheduled posts and recover stale state."""
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select, update

from app.core.database import async_session_factory
from app.core.security import decrypt_token, encrypt_token
from app.models.connected_account import ConnectedAccount
from app.models.post import Post, PostStatus
from app.services.storage_r2 import create_download_url
from app.services import x_oauth
from app.services.x_api import XApiService

logger = logging.getLogger(__name__)

_STALE_THRESHOLD = timedelta(minutes=10)
_TOKEN_REFRESH_BUFFER = timedelta(minutes=5)

_PERMANENT_PUBLISH_MESSAGES = (
    "No connected account linked to post",
    "missing media.write scope",
    "no refresh token is available",
)


async def publish_due_posts() -> None:
    """Claim all posts due now and publish them.

    Uses an atomic ``UPDATE ... RETURNING`` pattern so concurrent job
    instances (e.g. multiple workers) cannot double-process the same post.
    """
    async with async_session_factory() as session:
        stmt = (
            update(Post)
            .where(
                Post.status == PostStatus.scheduled,
                Post.is_deleted.is_(False),
                Post.scheduled_for <= datetime.now(timezone.utc),
            )
            .values(status=PostStatus.publishing)
            .returning(Post.id)
        )
        result = await session.execute(stmt)
        post_ids = list(result.scalars())
        await session.commit()

    if not post_ids:
        return

    logger.info("Claimed %d post(s) for publishing", len(post_ids))

    # Fetch claimed posts ordered by thread context so thread post N is
    # committed before post N+1 (D5 will use the prior platform_post_id).
    async with async_session_factory() as session:
        rows = await session.execute(
            select(Post)
            .where(Post.id.in_(post_ids))
            .order_by(Post.thread_id.nullslast(), Post.thread_order.nullslast())
        )
        posts = rows.scalars().all()

    for post in posts:
        await publish_single_post(post.id)


async def publish_single_post(post_id) -> tuple[str, str | None, bool, str | None]:
    """Attempt to publish one post and return status with retry metadata."""
    async with async_session_factory() as session:
        post = await session.get(Post, post_id)
        if not post:
            return (PostStatus.failed.value, "post not found", False, "POST_NOT_FOUND")

        account: ConnectedAccount | None = None
        if post.connected_account_id:
            account = await session.get(ConnectedAccount, post.connected_account_id)

        try:
            if not account:
                raise ValueError("No connected account linked to post")

            access_token = await _get_valid_access_token(account, session)
            api = XApiService(access_token=access_token)

            if post.media:
                _ensure_media_publish_scope(account)

            # Thread reply-chaining: post N must reply to post N-1.
            reply_to_id: str | None = None
            if post.thread_id and post.thread_order and post.thread_order > 1:
                prev_result = await session.execute(
                    select(Post).where(
                        Post.thread_id == post.thread_id,
                        Post.thread_order == post.thread_order - 1,
                    )
                )
                prev_post = prev_result.scalar_one_or_none()
                if not prev_post or not prev_post.platform_post_id:
                    raise ValueError(
                        f"Thread predecessor (order {post.thread_order - 1}) not yet published"
                    )
                reply_to_id = prev_post.platform_post_id

            media_ids = await _resolve_media_ids(post, api)

            if post.quote_of_platform_post_id:
                platform_post_id = await api.create_quote(
                    post.content,
                    post.quote_of_platform_post_id,
                    media_ids,
                )
            elif reply_to_id:
                platform_post_id = await api.create_reply(post.content, reply_to_id, media_ids)
            else:
                platform_post_id = await api.create_post(post.content, media_ids)

            post.status = PostStatus.published
            post.platform_post_id = platform_post_id
            post.published_at = datetime.now(timezone.utc)
            post.error_message = None

        except Exception as exc:
            logger.exception("Failed to publish post %s: %s", post_id, exc)
            post.status = PostStatus.failed
            post.error_message = str(exc)
            retryable, error_code = _classify_publish_error(exc)
            await session.commit()
            return (post.status.value, post.error_message, retryable, error_code)

        await session.commit()
        return (post.status.value, post.error_message, True, None)


def _classify_publish_error(exc: Exception) -> tuple[bool, str]:
    """Classify publish failures into retryable vs permanent."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429 or code >= 500:
            return (True, f"HTTP_{code}")
        if code in (400, 401, 403, 404):
            return (False, f"HTTP_{code}")
        return (True, f"HTTP_{code}")

    if isinstance(exc, (httpx.TimeoutException, httpx.RequestError)):
        return (True, "NETWORK_ERROR")

    msg = str(exc)
    for marker in _PERMANENT_PUBLISH_MESSAGES:
        if marker in msg:
            return (False, "AUTH_OR_ACCOUNT_CONFIGURATION")

    # Thread predecessor might be published shortly after; retry this case.
    if "Thread predecessor" in msg:
        return (True, "THREAD_PREDECESSOR_NOT_READY")

    return (True, "UNKNOWN")


async def _get_valid_access_token(account: ConnectedAccount, session) -> str:
    """Return a usable access token, refreshing it when it is near expiry."""
    now = datetime.now(timezone.utc)
    if (
        account.token_expires_at
        and account.token_expires_at <= now + _TOKEN_REFRESH_BUFFER
    ):
        if not account.refresh_token_enc:
            raise ValueError("X access token expired and no refresh token is available")

        refresh_token = decrypt_token(account.refresh_token_enc)
        token_data = await x_oauth.refresh_access_token(refresh_token)

        account.access_token_enc = encrypt_token(token_data["access_token"])
        if token_data.get("refresh_token"):
            account.refresh_token_enc = encrypt_token(token_data["refresh_token"])
        if token_data.get("expires_in"):
            account.token_expires_at = now + timedelta(seconds=token_data["expires_in"])
        if token_data.get("scope"):
            account.scopes = token_data["scope"]
        await session.flush()

    return decrypt_token(account.access_token_enc)


async def _resolve_media_ids(post: Post, api: XApiService) -> list[str] | None:
    """Upload stored R2 media to X and return X media IDs.

    Falls back to ``media_keys`` for legacy rows that already stored X media IDs.
    """
    if not post.media:
        return post.media_keys or None

    media_ids: list[str] = []
    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
        for media in post.media:
            file_key = media.get("key")
            public_url = media.get("public_url")
            source_url = create_download_url(file_key) if file_key else public_url
            if not source_url:
                raise ValueError("Post media is missing both key and public_url")

            response = await client.get(source_url)
            response.raise_for_status()
            content_type = media.get("content_type") or response.headers.get("Content-Type")
            if not content_type:
                raise ValueError(f"Could not determine content type for media {source_url}")

            media_id = await api.upload_media(response.content, content_type)
            media_ids.append(media_id)

    return media_ids or None


def _ensure_media_publish_scope(account: ConnectedAccount) -> None:
    scopes = {
        scope.strip()
        for scope in (account.scopes or "").split()
        if scope.strip()
    }
    if "media.write" not in scopes:
        raise ValueError(
            "Connected X account is missing media.write scope. "
            "Reconnect the X account to grant media upload permission."
        )


async def recover_stale_publishing() -> None:
    """Reset posts stuck in 'publishing' back to 'scheduled'.

    A post can be left in 'publishing' if the worker crashed after claiming
    it but before writing the outcome.  Posts older than ``_STALE_THRESHOLD``
    are safe to retry.
    """
    cutoff = datetime.now(timezone.utc) - _STALE_THRESHOLD
    async with async_session_factory() as session:
        stmt = (
            update(Post)
            .where(Post.status == PostStatus.publishing, Post.updated_at < cutoff)
            .values(status=PostStatus.scheduled)
        )
        result = await session.execute(stmt)
        await session.commit()

    if result.rowcount:
        logger.warning("Recovered %d stale publishing post(s)", result.rowcount)
