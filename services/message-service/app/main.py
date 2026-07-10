import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy.orm import Session

from . import crud, gateway_client, publisher, schemas
from .auth import get_current_user_id, require_internal_key
from .db import Base, engine, get_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Message Service", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/messages", response_model=schemas.MessageOut, status_code=status.HTTP_201_CREATED)
def send_message(
    payload: schemas.MessageCreate,
    db: Session = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    message = crud.create_message(
        db,
        from_user_id=current_user_id,
        to_user_id=payload.to_user_id,
        message=payload.message,
    )

    publisher.publish_message_created(
        {
            "message_id": str(message.id),
            "from_user_id": str(message.from_user_id),
            "to_user_id": str(message.to_user_id),
            "message": message.message,
            "created_at": message.created_at.isoformat(),
        }
    )

    return message


@app.get("/messages/{other_user_id}", response_model=list[schemas.MessageOut])
def get_conversation(
    other_user_id: uuid.UUID,
    limit: int = 50,
    before: datetime | None = None,
    db: Session = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    limit = max(1, min(limit, 200))

    newly_delivered = crud.mark_delivered_for_recipient(
        db, from_user_id=other_user_id, to_user_id=current_user_id
    )
    for message in newly_delivered:
        gateway_client.notify_delivered(message)

    return crud.get_conversation(db, current_user_id, other_user_id, limit=limit, before=before)


@app.patch(
    "/internal/messages/{message_id}/delivered",
    response_model=schemas.MessageOut,
    dependencies=[Depends(require_internal_key)],
)
def mark_delivered(message_id: uuid.UUID, db: Session = Depends(get_db)):
    message = crud.mark_delivered(db, message_id)
    if not message:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    return message


@app.patch(
    "/internal/messages/{message_id}/pending",
    response_model=schemas.MessageOut,
    dependencies=[Depends(require_internal_key)],
)
def mark_pending(message_id: uuid.UUID, db: Session = Depends(get_db)):
    message = crud.mark_pending(db, message_id)
    if not message:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    return message


@app.post(
    "/internal/messages/mark-delivered-for-user",
    response_model=list[schemas.MessageOut],
    dependencies=[Depends(require_internal_key)],
)
def mark_delivered_for_user(payload: schemas.MarkDeliveredForUserRequest, db: Session = Depends(get_db)):
    """Called by gateway-service right after a user's WebSocket connects -
    catches up every conversation with undelivered messages at once."""
    return crud.mark_delivered_for_user(db, payload.user_id)
