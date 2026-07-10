import logging

import httpx

from .config import settings

logger = logging.getLogger(__name__)


async def catch_up_pending_messages(user_id: str) -> list[dict]:
    """Called right after a user's WebSocket connects. Tells message-service
    to mark every SENT/PENDING message addressed to this user as DELIVERED
    in one shot - covers every conversation they were offline for, not just
    whichever one they happen to open first. Returns the updated messages
    so the caller can notify each original sender."""
    url = f"{settings.message_service_url}/internal/messages/mark-delivered-for-user"
    headers = {"X-Internal-Api-Key": settings.internal_api_key}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(url, json={"user_id": user_id}, headers=headers)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError:
        # Non-fatal: the REST-fetch fallback in message-service will
        # self-heal status the next time this user opens a conversation.
        logger.exception("Failed to catch up pending messages for user_id=%s", user_id)
        return []
