import jwt
from fastapi import Header, HTTPException, status

from .config import settings


class InvalidTokenError(Exception):
    pass


def decode_user_id(token: str) -> str:
    """Browsers can't set custom headers on the native WebSocket handshake,
    so the JWT travels as a query param (?token=...) instead of an
    Authorization header here."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise InvalidTokenError from exc

    user_id = payload.get("sub")
    if not user_id:
        raise InvalidTokenError("token missing sub claim")
    return user_id


def require_internal_key(x_internal_api_key: str | None = Header(default=None)) -> None:
    if x_internal_api_key != settings.internal_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid internal API key")
