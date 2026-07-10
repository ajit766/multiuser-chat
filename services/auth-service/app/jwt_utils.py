import time

import jwt

from .config import settings


def create_access_token(*, user_id: str, username: str) -> tuple[str, int]:
    expires_in = settings.jwt_expire_minutes * 60
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": now,
        "exp": now + expires_in,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_in
