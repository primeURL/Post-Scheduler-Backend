import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.post import PostStatus


class PostCreate(BaseModel):
    connected_account_id: uuid.UUID | None = None
    platform: str = Field(min_length=1, max_length=50)
    content: str = Field(min_length=1)
    scheduled_for: datetime | None = None
    thread_id: uuid.UUID | None = None
    thread_order: int | None = None
    media: list["PostMedia"] | None = None


class PostUpdate(BaseModel):
    connected_account_id: uuid.UUID | None = None
    content: str | None = Field(default=None, min_length=1)
    scheduled_for: datetime | None = None
    status: PostStatus | None = None
    media: list["PostMedia"] | None = None


class PostQuoteCreate(BaseModel):
    content: str = Field(min_length=1)
    connected_account_id: uuid.UUID | None = None
    scheduled_for: datetime | None = None
    media: list["PostMedia"] | None = None


class PostMedia(BaseModel):
    key: str = Field(min_length=1)
    public_url: str = Field(min_length=1)
    type: str = Field(min_length=1, max_length=50)
    content_type: str | None = Field(default=None, min_length=1, max_length=255)
    file_name: str | None = Field(default=None, min_length=1, max_length=255)
    size: int | None = Field(default=None, ge=1)


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
    reposted_at: datetime | None
    quote_of_platform_post_id: str | None
    error_message: str | None
    media_keys: list[str] | None
    media: list[PostMedia] | None
    is_deleted: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PostActionResult(BaseModel):
    success: bool = True
    message: str
    post_id: uuid.UUID
    platform_post_id: str | None = None
