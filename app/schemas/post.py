import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.post import PostStatus


class PostCreate(BaseModel):
    platform: str = Field(min_length=2, max_length=50)
    content: str = Field(min_length=1)
    scheduled_for: datetime | None = None
    thread_id: uuid.UUID | None = None
    thread_order: int | None = None


class PostUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1)
    scheduled_for: datetime | None = None
    status: PostStatus | None = None


class PostRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    connected_account_id: uuid.UUID | None
    platform: str
    content: str
    status: PostStatus
    thread_id: uuid.UUID | None
    thread_order: int | None
    scheduled_for: datetime | None
    published_at: datetime | None
    platform_post_id: str | None
    error_message: str | None
    media_keys: list[str] | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
