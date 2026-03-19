import uuid

from pydantic import BaseModel, Field


class UploadUrlRequest(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=255)
    size: int | None = Field(default=None, ge=1)


class UploadUrlResponse(BaseModel):
    upload_url: str
    public_url: str
    file_key: str
    content_type: str
    expires_in: int
    user_id: uuid.UUID
