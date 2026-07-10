import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models


def get_user_by_username(db: Session, username: str) -> models.User | None:
    return db.scalar(select(models.User).where(models.User.username == username))


def list_users(db: Session, exclude_user_id: str | uuid.UUID | None = None) -> list[models.User]:
    stmt = select(models.User).order_by(models.User.username)
    if exclude_user_id is not None:
        if isinstance(exclude_user_id, str):
            exclude_user_id = uuid.UUID(exclude_user_id)
        stmt = stmt.where(models.User.id != exclude_user_id)
    return list(db.scalars(stmt))


def create_user(
    db: Session, *, username: str, password_hash: str, first_name: str, last_name: str
) -> models.User:
    user = models.User(
        username=username,
        password_hash=password_hash,
        first_name=first_name,
        last_name=last_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
