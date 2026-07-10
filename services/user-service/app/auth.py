import jwt
from fastapi import Header, HTTPException, status

from .config import settings


def get_current_user_id(authorization: str | None = Header(default=None)) -> str:
    """Decodes the JWT issued by Auth Service. Every service that needs to know
    'who is calling' validates the token locally against the shared signing
    secret rather than calling Auth Service on every request."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid Authorization header")

    token = authorization.removeprefix("Bearer ")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token payload")
    return user_id


def require_internal_key(x_internal_api_key: str | None = Header(default=None)) -> None:
    """Guards service-to-service-only endpoints (e.g. Auth Service reading
    password hashes). Not exposed publicly through Nginx in v1, but checked
    anyway as defense in depth."""
    if x_internal_api_key != settings.internal_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid internal API key")
