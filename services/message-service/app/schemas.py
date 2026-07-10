import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MessageCreate(BaseModel):
    to_user_id: uuid.UUID
    message: str = Field(min_length=1, max_length=4000)


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    from_user_id: uuid.UUID
    to_user_id: uuid.UUID
    message: str
    status: str
    created_at: datetime
    delivered_at: datetime | None = None


class MarkDeliveredForUserRequest(BaseModel):
    user_id: uuid.UUID
