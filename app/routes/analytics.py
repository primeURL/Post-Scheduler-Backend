"""Analytics routes — read-only access to post performance metrics."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.post import Post
from app.models.post_analytics import PostAnalytics
from app.models.user import User
from app.schemas.analytics import PostAnalyticsRead

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/posts/{post_id}", response_model=list[PostAnalyticsRead])
async def get_post_analytics(
    post_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[PostAnalytics]:
    """Return all analytics snapshots for a post owned by the current user."""
    post = await db.get(Post, post_id)
    if not post or post.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    result = await db.execute(
        select(PostAnalytics)
        .where(PostAnalytics.post_id == post_id)
        .order_by(PostAnalytics.fetched_at.asc())
    )
    return list(result.scalars().all())
