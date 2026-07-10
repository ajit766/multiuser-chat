import uuid

import pytest
from fakeredis import aioredis as fake_aioredis
from fastapi.testclient import TestClient

from app import redis_client
from app.config import settings
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
    fake = fake_aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_client, "redis_client", fake)
    return fake


def test_online_requires_internal_key():
    user_id = str(uuid.uuid4())
    r = client.post("/presence/online", json={"user_id": user_id})
    assert r.status_code == 401


def test_mark_online_then_get_presence():
    user_id = str(uuid.uuid4())
    headers = {"X-Internal-Api-Key": settings.internal_api_key}

    r = client.post("/presence/online", json={"user_id": user_id}, headers=headers)
    assert r.status_code == 204

    r2 = client.get(f"/presence/{user_id}", headers=headers)
    assert r2.status_code == 200
    assert r2.json() == {"user_id": user_id, "online": True}


def test_mark_offline_clears_presence():
    user_id = str(uuid.uuid4())
    headers = {"X-Internal-Api-Key": settings.internal_api_key}

    client.post("/presence/online", json={"user_id": user_id}, headers=headers)
    r = client.post("/presence/offline", json={"user_id": user_id}, headers=headers)
    assert r.status_code == 204

    r2 = client.get(f"/presence/{user_id}", headers=headers)
    assert r2.json() == {"user_id": user_id, "online": False}


def test_unknown_user_is_offline():
    user_id = str(uuid.uuid4())
    headers = {"X-Internal-Api-Key": settings.internal_api_key}

    r = client.get(f"/presence/{user_id}", headers=headers)
    assert r.json()["online"] is False
