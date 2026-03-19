import uuid
from datetime import datetime

from pydantic import BaseModel


class PostAnalyticsRead(BaseModel):
    id: uuid.UUID
    post_id: uuid.UUID
    fetched_at: datetime
    impressions: int
    likes: int
    retweets: int
    replies: int
    quoted_count: int
    bookmarks: int
    clicks: int
    profile_visits: int

    model_config = {"from_attributes": True}


class PostAnalyticsLatestRead(BaseModel):
    post_id: uuid.UUID
    x_post_id: str | None
    content: str
    status: str
    is_deleted: bool
    scheduled_for: datetime | None
    published_at: datetime | None
    fetched_at: datetime | None
    impression_count: int
    like_count: int
    repost_count: int
    reply_count: int
    quoted_count: int
    bookmarks: int
    has_repost_action: bool
    has_quote_action: bool
