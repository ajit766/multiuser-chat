import bcrypt
from fastapi.testclient import TestClient

from app import user_client
from app.main import app

client = TestClient(app)

FAKE_USER = {
    "id": "8729bbe8-61f5-4153-8dd2-2c4360fb8209",
    "username": "alice",
    "first_name": "Alice",
    "last_name": "A",
    "password_hash": bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode(),
}


async def _found(username: str):
    return FAKE_USER if username == "alice" else None


async def _not_found(username: str):
    return None


def test_login_success(monkeypatch):
    monkeypatch.setattr(user_client, "get_user_by_username", _found)
    r = client.post("/auth/login", json={"username": "alice", "password": "password123"})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["username"] == "alice"
    assert "password_hash" not in body["user"]


def test_login_wrong_password(monkeypatch):
    monkeypatch.setattr(user_client, "get_user_by_username", _found)
    r = client.post("/auth/login", json={"username": "alice", "password": "wrong-password"})
    assert r.status_code == 401


def test_login_unknown_user(monkeypatch):
    monkeypatch.setattr(user_client, "get_user_by_username", _not_found)
    r = client.post("/auth/login", json={"username": "ghost", "password": "password123"})
    assert r.status_code == 401


def test_logout_returns_no_content():
    r = client.post("/auth/logout")
    assert r.status_code == 204
