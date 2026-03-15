import uuid
from datetime import datetime

from pydantic import BaseModel


class TokenResponse(BaseModel):
    """Returned from /auth/refresh — access token only; refresh token lives in httpOnly cookie."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class UserRead(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    avatar_url: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
