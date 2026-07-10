import httpx
from fastapi import FastAPI, HTTPException, status

from . import security, user_client
from .jwt_utils import create_access_token
from .schemas import LoginRequest, TokenResponse, UserPublic

app = FastAPI(title="Auth Service")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/auth/login", response_model=TokenResponse)
async def login(payload: LoginRequest):
    try:
        user = await user_client.get_user_by_username(payload.username)
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "User service unavailable")

    if not user or not security.verify_password(payload.password, user["password_hash"]):
        # Same error for "no such user" and "wrong password" so we don't leak
        # which one it was.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")

    token, expires_in = create_access_token(user_id=user["id"], username=user["username"])
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserPublic(**user),
    )


@app.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout():
    # JWTs are stateless in v1, so logout is client-side (discard the
    # token). A Redis-backed revocation blocklist would be the next step
    # if a real server-side logout becomes necessary.
    return None
