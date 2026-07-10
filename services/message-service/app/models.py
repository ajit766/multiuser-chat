import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class MessageStatus(str, enum.Enum):
    SENT = "SENT"
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    from_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    to_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    message: Mapped[str] = mapped_column(String(4000), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=MessageStatus.SENT.value)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
