import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from . import models


def _as_uuid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def create_message(db: Session, *, from_user_id, to_user_id, message: str) -> models.Message:
    msg = models.Message(
        from_user_id=_as_uuid(from_user_id),
        to_user_id=_as_uuid(to_user_id),
        message=message,
        status=models.MessageStatus.SENT.value,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def get_conversation(
    db: Session, user_a, user_b, *, limit: int = 50, before: datetime | None = None
) -> list[models.Message]:
    user_a, user_b = _as_uuid(user_a), _as_uuid(user_b)
    stmt = select(models.Message).where(
        or_(
            and_(models.Message.from_user_id == user_a, models.Message.to_user_id == user_b),
            and_(models.Message.from_user_id == user_b, models.Message.to_user_id == user_a),
        )
    )
    if before is not None:
        stmt = stmt.where(models.Message.created_at < before)
    stmt = stmt.order_by(models.Message.created_at.desc()).limit(limit)

    rows = list(db.scalars(stmt))
    rows.reverse()
    return rows


def mark_delivered(db: Session, message_id) -> models.Message | None:
    message = db.get(models.Message, _as_uuid(message_id))
    if not message:
        return None
    message.status = models.MessageStatus.DELIVERED.value
    message.delivered_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(message)
    return message


def mark_pending(db: Session, message_id) -> models.Message | None:
    """delivery-service calls this when the recipient was offline (or the
    push otherwise failed) at the moment the message was created - an
    explicit 'we know delivery didn't happen yet' state, distinct from
    freshly-SENT-but-not-processed-yet, mainly for observability and to
    give the reconnect catch-up query (mark_delivered_for_user) a precise
    target."""
    message = db.get(models.Message, _as_uuid(message_id))
    if not message:
        return None
    message.status = models.MessageStatus.PENDING.value
    db.commit()
    db.refresh(message)
    return message


_UNDELIVERED_STATUSES = (models.MessageStatus.SENT.value, models.MessageStatus.PENDING.value)


def mark_delivered_for_user(db: Session, user_id) -> list[models.Message]:
    """Called by gateway-service the moment `user_id`'s WebSocket connects.
    Catches up EVERY pending conversation at once (not just whichever one
    they happen to open first) - this is the primary delivery-confirmation
    path. Includes SENT as well as PENDING to cover the tiny race window
    where delivery-service hasn't processed the message.created event yet."""
    user_id = _as_uuid(user_id)
    stmt = select(models.Message).where(
        models.Message.to_user_id == user_id,
        models.Message.status.in_(_UNDELIVERED_STATUSES),
    )
    undelivered = list(db.scalars(stmt))
    if not undelivered:
        return []

    now = datetime.now(timezone.utc)
    for message in undelivered:
        message.status = models.MessageStatus.DELIVERED.value
        message.delivered_at = now
    db.commit()
    for message in undelivered:
        db.refresh(message)
    return undelivered


def mark_delivered_for_recipient(db: Session, *, from_user_id, to_user_id) -> list[models.Message]:
    """Called when `to_user_id` fetches their conversation with
    `from_user_id` (GET /messages/{other_user_id}). This is now a fallback
    safety net behind mark_delivered_for_user (the WS-connect path) - it
    self-heals status in the rare case the connect-time catch-up call
    failed (e.g. message-service was briefly down at connect time)."""
    from_user_id, to_user_id = _as_uuid(from_user_id), _as_uuid(to_user_id)
    stmt = select(models.Message).where(
        models.Message.from_user_id == from_user_id,
        models.Message.to_user_id == to_user_id,
        models.Message.status.in_(_UNDELIVERED_STATUSES),
    )
    pending = list(db.scalars(stmt))
    if not pending:
        return []

    now = datetime.now(timezone.utc)
    for message in pending:
        message.status = models.MessageStatus.DELIVERED.value
        message.delivered_at = now
    db.commit()
    for message in pending:
        db.refresh(message)
    return pending
