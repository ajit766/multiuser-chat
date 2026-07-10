import uuid

from pydantic import BaseModel


class PresenceRequest(BaseModel):
    user_id: uuid.UUID


class PresenceStatus(BaseModel):
    user_id: uuid.UUID
    online: bool
