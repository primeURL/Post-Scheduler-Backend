import uuid
from datetime import datetime

from pydantic import BaseModel


class ConnectedAccountRead(BaseModel):
    id: uuid.UUID
    platform: str
    platform_user_id: str
    platform_username: str
    scopes: str | None
    token_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class XConnectResponse(BaseModel):
    authorization_url: str
