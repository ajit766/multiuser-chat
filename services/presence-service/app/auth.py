from fastapi import Header, HTTPException, status

from .config import settings


def require_internal_key(x_internal_api_key: str | None = Header(default=None)) -> None:
    """Presence Service has no public routes in v1 - only Gateway Service
    (on connect/disconnect) and Delivery Service (checking recipient status)
    call it, over the internal Docker network. This header check is defense
    in depth on top of that network boundary."""
    if x_internal_api_key != settings.internal_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid internal API key")
