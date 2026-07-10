import logging

import httpx

from .config import settings

logger = logging.getLogger(__name__)

_HEADERS = {"X-Internal-Api-Key": settings.internal_api_key}


def is_user_online(user_id: str) -> bool:
    url = f"{settings.presence_service_url}/presence/{user_id}"
    try:
        response = httpx.get(url, headers=_HEADERS, timeout=5.0)
        response.raise_for_status()
        return response.json().get("online", False)
    except httpx.HTTPError:
        logger.exception("Failed to check presence for user_id=%s", user_id)
        return False


def push_to_gateway(user_id: str, payload: dict) -> bool:
    url = f"{settings.gateway_service_url}/internal/push"
    try:
        response = httpx.post(
            url, json={"user_id": user_id, "payload": payload}, headers=_HEADERS, timeout=5.0
        )
        response.raise_for_status()
        return response.json().get("delivered", False)
    except httpx.HTTPError:
        logger.exception("Failed to push message to user_id=%s via gateway", user_id)
        return False


def mark_message_delivered(message_id: str) -> None:
    url = f"{settings.message_service_url}/internal/messages/{message_id}/delivered"
    try:
        response = httpx.patch(url, headers=_HEADERS, timeout=5.0)
        response.raise_for_status()
    except httpx.HTTPError:
        logger.exception("Failed to mark message_id=%s delivered", message_id)


def mark_message_pending(message_id: str) -> None:
    url = f"{settings.message_service_url}/internal/messages/{message_id}/pending"
    try:
        response = httpx.patch(url, headers=_HEADERS, timeout=5.0)
        response.raise_for_status()
    except httpx.HTTPError:
        logger.exception("Failed to mark message_id=%s pending", message_id)
