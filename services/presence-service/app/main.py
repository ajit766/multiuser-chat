import uuid

from fastapi import Depends, FastAPI

from . import redis_client
from .auth import require_internal_key
from .config import settings
from .schemas import PresenceRequest, PresenceStatus

app = FastAPI(title="Presence Service")


def _key(user_id) -> str:
    return f"presence:{user_id}"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/presence/online", status_code=204, dependencies=[Depends(require_internal_key)])
async def mark_online(payload: PresenceRequest):
    await redis_client.redis_client.set(_key(payload.user_id), "online", ex=settings.presence_ttl_seconds)


@app.post("/presence/offline", status_code=204, dependencies=[Depends(require_internal_key)])
async def mark_offline(payload: PresenceRequest):
    await redis_client.redis_client.delete(_key(payload.user_id))


@app.get(
    "/presence/{user_id}",
    response_model=PresenceStatus,
    dependencies=[Depends(require_internal_key)],
)
async def get_presence(user_id: uuid.UUID):
    value = await redis_client.redis_client.get(_key(user_id))
    return PresenceStatus(user_id=user_id, online=value is not None)
