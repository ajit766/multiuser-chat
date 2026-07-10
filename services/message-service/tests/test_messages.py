import time
import uuid

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import gateway_client, publisher
from app.config import settings
from app.db import Base, get_db
from app.main import app

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

ALICE_ID = str(uuid.uuid4())
BOB_ID = str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _fresh_schema():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(autouse=True)
def _no_real_rabbitmq(monkeypatch):
    published = []
    monkeypatch.setattr(publisher, "publish_message_created", lambda payload: published.append(payload))
    return published


@pytest.fixture(autouse=True)
def _no_real_gateway_calls(monkeypatch):
    notified = []
    monkeypatch.setattr(gateway_client, "notify_delivered", lambda message: notified.append(message))
    return notified


def _token_for(user_id: str) -> str:
    now = int(time.time())
    payload = {"sub": user_id, "username": "x", "iat": now, "exp": now + 3600}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def test_send_message_requires_auth():
    r = client.post("/messages", json={"to_user_id": BOB_ID, "message": "hi"})
    assert r.status_code == 401


def test_send_message_persists_and_publishes(_no_real_rabbitmq):
    token = _token_for(ALICE_ID)
    r = client.post(
        "/messages",
        json={"to_user_id": BOB_ID, "message": "hello bob"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "SENT"
    assert body["from_user_id"] == ALICE_ID

    assert len(_no_real_rabbitmq) == 1
    assert _no_real_rabbitmq[0]["message_id"] == body["id"]


def test_conversation_visible_from_both_sides():
    token_alice = _token_for(ALICE_ID)
    token_bob = _token_for(BOB_ID)

    client.post(
        "/messages",
        json={"to_user_id": BOB_ID, "message": "hi bob"},
        headers={"Authorization": f"Bearer {token_alice}"},
    )
    client.post(
        "/messages",
        json={"to_user_id": ALICE_ID, "message": "hi alice"},
        headers={"Authorization": f"Bearer {token_bob}"},
    )

    r = client.get(f"/messages/{BOB_ID}", headers={"Authorization": f"Bearer {token_alice}"})
    assert r.status_code == 200
    texts = [m["message"] for m in r.json()]
    assert texts == ["hi bob", "hi alice"]


def test_fetching_conversation_marks_received_messages_delivered(_no_real_gateway_calls):
    token_alice = _token_for(ALICE_ID)
    token_bob = _token_for(BOB_ID)

    # Alice sends while Bob is "offline" - delivery-service would have
    # left this as SENT since there was no live socket to push to.
    send = client.post(
        "/messages",
        json={"to_user_id": BOB_ID, "message": "hi bob, are you there?"},
        headers={"Authorization": f"Bearer {token_alice}"},
    )
    message_id = send.json()["id"]
    assert send.json()["status"] == "SENT"

    # Bob later opens the conversation and fetches history.
    r = client.get(f"/messages/{ALICE_ID}", headers={"Authorization": f"Bearer {token_bob}"})
    assert r.status_code == 200
    fetched = next(m for m in r.json() if m["id"] == message_id)
    assert fetched["status"] == "DELIVERED"

    # Alice (the sender) should have been notified so her tick updates live.
    assert len(_no_real_gateway_calls) == 1
    assert str(_no_real_gateway_calls[0].id) == message_id


def test_mark_delivered_requires_internal_key():
    token = _token_for(ALICE_ID)
    r = client.post(
        "/messages",
        json={"to_user_id": BOB_ID, "message": "hi"},
        headers={"Authorization": f"Bearer {token}"},
    )
    message_id = r.json()["id"]

    r2 = client.patch(f"/internal/messages/{message_id}/delivered")
    assert r2.status_code == 401

    r3 = client.patch(
        f"/internal/messages/{message_id}/delivered",
        headers={"X-Internal-Api-Key": settings.internal_api_key},
    )
    assert r3.status_code == 200
    assert r3.json()["status"] == "DELIVERED"


def test_mark_pending_requires_internal_key():
    token = _token_for(ALICE_ID)
    r = client.post(
        "/messages",
        json={"to_user_id": BOB_ID, "message": "hi"},
        headers={"Authorization": f"Bearer {token}"},
    )
    message_id = r.json()["id"]

    r2 = client.patch(f"/internal/messages/{message_id}/pending")
    assert r2.status_code == 401

    r3 = client.patch(
        f"/internal/messages/{message_id}/pending",
        headers={"X-Internal-Api-Key": settings.internal_api_key},
    )
    assert r3.status_code == 200
    assert r3.json()["status"] == "PENDING"


def test_mark_delivered_for_user_requires_internal_key():
    r = client.post("/internal/messages/mark-delivered-for-user", json={"user_id": str(uuid.uuid4())})
    assert r.status_code == 401


def test_mark_delivered_for_user_catches_up_all_senders_at_once():
    carol_id = str(uuid.uuid4())
    dave_id = str(uuid.uuid4())
    token_alice = _token_for(ALICE_ID)
    token_dave = _token_for(dave_id)
    internal_headers = {"X-Internal-Api-Key": settings.internal_api_key}

    m1 = client.post(
        "/messages",
        json={"to_user_id": carol_id, "message": "from alice"},
        headers={"Authorization": f"Bearer {token_alice}"},
    ).json()
    m2 = client.post(
        "/messages",
        json={"to_user_id": carol_id, "message": "from dave"},
        headers={"Authorization": f"Bearer {token_dave}"},
    ).json()

    # Simulate delivery-service having found Carol offline for both sends.
    client.patch(f"/internal/messages/{m1['id']}/pending", headers=internal_headers)
    client.patch(f"/internal/messages/{m2['id']}/pending", headers=internal_headers)

    # Carol's socket connects - gateway-service calls this bulk endpoint
    # once, and it should catch up both conversations in a single shot.
    r = client.post(
        "/internal/messages/mark-delivered-for-user",
        json={"user_id": carol_id},
        headers=internal_headers,
    )
    assert r.status_code == 200
    delivered_ids = {m["id"] for m in r.json()}
    assert delivered_ids == {m1["id"], m2["id"]}
    assert all(m["status"] == "DELIVERED" for m in r.json())
