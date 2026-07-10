import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app import message_client, presence_client
from app.config import settings
from app.connection_manager import manager
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fake_presence(monkeypatch):
    calls = []

    async def fake_online(user_id):
        calls.append(("online", user_id))

    async def fake_offline(user_id):
        calls.append(("offline", user_id))

    monkeypatch.setattr(presence_client, "mark_online", fake_online)
    monkeypatch.setattr(presence_client, "mark_offline", fake_offline)
    yield calls


@pytest.fixture(autouse=True)
def _no_pending_messages(monkeypatch):
    async def fake_catch_up(user_id):
        return []

    monkeypatch.setattr(message_client, "catch_up_pending_messages", fake_catch_up)


@pytest.fixture(autouse=True)
def _reset_manager():
    yield
    manager._connections.clear()


def _token_for(user_id: str) -> str:
    now = int(time.time())
    payload = {"sub": user_id, "username": "x", "iat": now, "exp": now + 3600}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def test_websocket_rejects_invalid_token():
    with pytest.raises(Exception):
        with client.websocket_connect("/ws?token=not-a-real-token"):
            pass


def test_connect_marks_online_ping_pong_and_disconnect_marks_offline(_fake_presence):
    token = _token_for("alice-id")
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

    assert ("online", "alice-id") in _fake_presence
    assert ("offline", "alice-id") in _fake_presence


def test_internal_push_delivers_to_connected_user():
    token = _token_for("bob-id")
    with client.websocket_connect(f"/ws?token={token}") as ws:
        r = client.post(
            "/internal/push",
            json={"user_id": "bob-id", "payload": {"type": "message", "message": "hi"}},
            headers={"X-Internal-Api-Key": settings.internal_api_key},
        )
        assert r.status_code == 200
        assert r.json() == {"delivered": True}

        assert ws.receive_json() == {"type": "message", "message": "hi"}


def test_internal_push_reports_not_delivered_when_user_offline():
    r = client.post(
        "/internal/push",
        json={"user_id": "ghost-id", "payload": {"type": "message"}},
        headers={"X-Internal-Api-Key": settings.internal_api_key},
    )
    assert r.status_code == 200
    assert r.json() == {"delivered": False}


def test_internal_push_requires_internal_key():
    r = client.post("/internal/push", json={"user_id": "bob-id", "payload": {}})
    assert r.status_code == 401


def test_reconnect_replaces_old_socket(_fake_presence):
    token = _token_for("carol-id")
    with client.websocket_connect(f"/ws?token={token}") as first_ws:
        with client.websocket_connect(f"/ws?token={token}") as second_ws:
            r = client.post(
                "/internal/push",
                json={"user_id": "carol-id", "payload": {"type": "message", "message": "hey"}},
                headers={"X-Internal-Api-Key": settings.internal_api_key},
            )
            assert r.json() == {"delivered": True}
            assert second_ws.receive_json() == {"type": "message", "message": "hey"}


def test_connect_catches_up_pending_messages_and_notifies_sender(monkeypatch):
    # Alice connects first (in this test) with nothing pending for her;
    # Bob's connect is the one with a pending message waiting.
    async def fake_catch_up(user_id):
        if user_id == "bob-id":
            return [
                {"id": "m1", "from_user_id": "alice-id", "to_user_id": "bob-id", "status": "DELIVERED"}
            ]
        return []

    monkeypatch.setattr(message_client, "catch_up_pending_messages", fake_catch_up)

    alice_token = _token_for("alice-id")
    with client.websocket_connect(f"/ws?token={alice_token}") as alice_ws:
        # Bob comes online - his pending message from Alice gets caught up,
        # and Alice (the sender, already connected) should be notified live.
        bob_token = _token_for("bob-id")
        with client.websocket_connect(f"/ws?token={bob_token}"):
            assert alice_ws.receive_json() == {
                "type": "status_update",
                "data": {"id": "m1", "to_user_id": "bob-id", "status": "DELIVERED"},
            }


def test_connect_with_no_pending_messages_is_a_noop(_fake_presence):
    token = _token_for("dave-id")
    with client.websocket_connect(f"/ws?token={token}"):
        pass  # would hang/fail if catch-up tried to push anything unexpected

    assert ("online", "dave-id") in _fake_presence
