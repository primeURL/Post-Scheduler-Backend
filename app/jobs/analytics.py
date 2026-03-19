"""Background job: snapshot tweet engagement metrics into post_analytics."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.security import decrypt_token
from app.models.connected_account import ConnectedAccount
from app.models.post import Post, PostStatus
from app.models.post_analytics import PostAnalytics
from app.services.x_api import XApiService

logger = logging.getLogger(__name__)

# Only snapshot posts published within this window (X free tier is rate-limited).
_ANALYTICS_WINDOW_DAYS = 7
_BATCH_SIZE = 50


async def fetch_published_analytics() -> None:
    """Snapshot engagement metrics for recently published posts.

    Runs every 6 hours.  Processes at most ``_BATCH_SIZE`` posts per run
    to stay within X API free-tier rate limits.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=_ANALYTICS_WINDOW_DAYS)

    async with async_session_factory() as session:
        result = await session.execute(
            select(Post, ConnectedAccount)
            .join(ConnectedAccount, Post.connected_account_id == ConnectedAccount.id)
            .where(
                Post.status == PostStatus.published,
                Post.is_deleted.is_(False),
                Post.platform_post_id.is_not(None),
                Post.published_at >= cutoff,
            )
            .limit(_BATCH_SIZE)
        )
        rows = result.all()

    if not rows:
        return

    logger.info("Fetching analytics for %d published post(s)", len(rows))
    for post, account in rows:
        await _save_snapshot(post, account)


async def _save_snapshot(post: Post, account: ConnectedAccount) -> None:
    """Fetch metrics for one post and write a PostAnalytics snapshot."""
    if post.is_deleted:
        return

    try:
        access_token = decrypt_token(account.access_token_enc)
        api = XApiService(access_token=access_token)
        metrics = await api.get_tweet_metrics(post.platform_post_id)
    except Exception as exc:
        logger.warning("Analytics fetch failed for post %s: %s", post.id, exc)
        return

    async with async_session_factory() as session:
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
        session.add(snapshot)
        await session.commit()
