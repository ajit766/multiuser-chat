import time

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.main import app

# In-memory SQLite for tests. `app`'s lifespan (which creates tables against
# the real Postgres engine) is never triggered here because we don't use
# TestClient as a context manager, so this stays fully isolated.
test_engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_schema():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


def _register(username="alice", **overrides):
    payload = {
        "username": username,
        "password": "password123",
        "first_name": "Alice",
        "last_name": "A",
        **overrides,
    }
    return client.post("/users", json=payload)


def _token_for(user_id: str) -> str:
    now = int(time.time())
    payload = {"sub": str(user_id), "username": "alice", "iat": now, "exp": now + 3600}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def test_register_user_succeeds():
    r = _register()
    assert r.status_code == 201
    body = r.json()
    assert body["username"] == "alice"
    assert "password" not in body
    assert "password_hash" not in body


def test_register_duplicate_username_rejected():
    _register("carol")
    r = _register("carol")
    assert r.status_code == 409


def test_register_missing_fields_rejected():
    r = client.post("/users", json={"username": "dave"})
    assert r.status_code == 422


def test_list_users_requires_auth():
    r = client.get("/users")
    assert r.status_code == 401


def test_list_users_excludes_self():
    alice = _register("alice").json()
    _register("bob")

    token = _token_for(alice["id"])
    r = client.get("/users", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    usernames = [u["username"] for u in r.json()]
    assert usernames == ["bob"]


def test_internal_endpoint_requires_internal_key():
    _register("alice")
    r = client.get("/internal/users/by-username/alice")
    assert r.status_code == 401


def test_internal_endpoint_returns_password_hash_with_key():
    _register("alice")
    r = client.get(
        "/internal/users/by-username/alice",
        headers={"X-Internal-Api-Key": settings.internal_api_key},
    )
    assert r.status_code == 200
    assert "password_hash" in r.json()
