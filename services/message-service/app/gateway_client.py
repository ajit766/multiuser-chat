import logging

import httpx

from . import models
from .config import settings

logger = logging.getLogger(__name__)


def notify_delivered(message: models.Message) -> None:
    """Best-effort live nudge to the sender when we mark a message
    DELIVERED because the recipient just fetched history (as opposed to
    getting a real-time push). If the sender isn't connected right now,
    this is a no-op - their next fetch will show the correct status
    anyway since it's already persisted."""
    url = f"{settings.gateway_service_url}/internal/push"
    headers = {"X-Internal-Api-Key": settings.internal_api_key}
    payload = {
        "user_id": str(message.from_user_id),
        "payload": {
            "type": "status_update",
            "data": {
                "id": str(message.id),
                "to_user_id": str(message.to_user_id),
                "status": "DELIVERED",
            },
        },
    }
    try:
        httpx.post(url, json=payload, headers=headers, timeout=5.0)
    except httpx.HTTPError:
        logger.exception(
            "Failed to notify sender %s of delivery for message %s",
            message.from_user_id,
            message.id,
        )
