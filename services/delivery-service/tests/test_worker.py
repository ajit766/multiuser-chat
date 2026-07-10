from unittest.mock import MagicMock

from app import clients, worker

EVENT = {
    "message_id": "m1",
    "from_user_id": "u1",
    "to_user_id": "u2",
    "message": "hi",
    "created_at": "2024-01-01T00:00:00Z",
}


def test_delivers_when_recipient_online(monkeypatch):
    monkeypatch.setattr(clients, "is_user_online", lambda user_id: True)
    push_mock = MagicMock(return_value=True)
    monkeypatch.setattr(clients, "push_to_gateway", push_mock)
    mark_mock = MagicMock()
    monkeypatch.setattr(clients, "mark_message_delivered", mark_mock)

    worker.handle_message_created(EVENT)

    # Once to push the message to the recipient, once to notify the sender
    # their tick should flip to double.
    assert push_mock.call_count == 2
    recipient_call, sender_call = push_mock.call_args_list
    assert recipient_call.args[0] == "u2"
    assert recipient_call.args[1]["type"] == "message"
    assert sender_call.args[0] == "u1"
    assert sender_call.args[1] == {
        "type": "status_update",
        "data": {"id": "m1", "to_user_id": "u2", "status": "DELIVERED"},
    }
    mark_mock.assert_called_once_with("m1")


def test_marks_pending_when_recipient_offline(monkeypatch):
    monkeypatch.setattr(clients, "is_user_online", lambda user_id: False)
    push_mock = MagicMock()
    monkeypatch.setattr(clients, "push_to_gateway", push_mock)
    mark_delivered_mock = MagicMock()
    monkeypatch.setattr(clients, "mark_message_delivered", mark_delivered_mock)
    mark_pending_mock = MagicMock()
    monkeypatch.setattr(clients, "mark_message_pending", mark_pending_mock)

    worker.handle_message_created(EVENT)

    push_mock.assert_not_called()
    mark_delivered_mock.assert_not_called()
    mark_pending_mock.assert_called_once_with("m1")


def test_marks_pending_when_push_fails(monkeypatch):
    monkeypatch.setattr(clients, "is_user_online", lambda user_id: True)
    monkeypatch.setattr(clients, "push_to_gateway", lambda user_id, payload: False)
    mark_delivered_mock = MagicMock()
    monkeypatch.setattr(clients, "mark_message_delivered", mark_delivered_mock)
    mark_pending_mock = MagicMock()
    monkeypatch.setattr(clients, "mark_message_pending", mark_pending_mock)

    worker.handle_message_created(EVENT)

    mark_delivered_mock.assert_not_called()
    mark_pending_mock.assert_called_once_with("m1")
