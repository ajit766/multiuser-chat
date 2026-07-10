import logging

import httpx

from .config import settings

logger = logging.getLogger(__name__)


async def mark_online(user_id: str) -> None:
    await _post("/presence/online", user_id)


async def mark_offline(user_id: str) -> None:
    await _post("/presence/offline", user_id)


async def _post(path: str, user_id: str) -> None:
    # A failed presence update shouldn't drop the socket connection - it
    # just means the recipient may look offline to Delivery Service until
    # the next successful heartbeat.
    url = f"{settings.presence_service_url}{path}"
    headers = {"X-Internal-Api-Key": settings.internal_api_key}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json={"user_id": user_id}, headers=headers)
    except httpx.HTTPError:
        logger.exception("Failed to update presence for user_id=%s path=%s", user_id, path)
