import httpx

from .config import settings


async def get_user_by_username(username: str) -> dict | None:
    """Auth Service owns no user data itself (fixes the 'Auth and User share
    a DB' design gap) - it asks User Service for the credential record it
    needs, over a real internal HTTP call."""
    url = f"{settings.user_service_url}/internal/users/by-username/{username}"
    headers = {"X-Internal-Api-Key": settings.internal_api_key}

    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(url, headers=headers)

    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()
