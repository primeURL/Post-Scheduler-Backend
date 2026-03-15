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
    clicks: int
    profile_visits: int

    model_config = {"from_attributes": True}
