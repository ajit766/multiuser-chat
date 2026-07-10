from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import crud, schemas, security
from .auth import get_current_user_id, require_internal_key
from .db import Base, engine, get_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="User Service", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/users", response_model=schemas.UserPublic, status_code=status.HTTP_201_CREATED)
def register_user(payload: schemas.UserRegister, db: Session = Depends(get_db)):
    if crud.get_user_by_username(db, payload.username):
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")

    try:
        user = crud.create_user(
            db,
            username=payload.username,
            password_hash=security.hash_password(payload.password),
            first_name=payload.first_name,
            last_name=payload.last_name,
        )
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")

    return user


@app.get("/users", response_model=list[schemas.UserPublic])
def get_users(
    db: Session = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    return crud.list_users(db, exclude_user_id=current_user_id)


@app.get(
    "/internal/users/by-username/{username}",
    response_model=schemas.UserInternal,
    dependencies=[Depends(require_internal_key)],
)
def get_user_internal(username: str, db: Session = Depends(get_db)):
    user = crud.get_user_by_username(db, username)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user
