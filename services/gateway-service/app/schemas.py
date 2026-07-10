from pydantic import BaseModel


class PushRequest(BaseModel):
    user_id: str
    payload: dict
