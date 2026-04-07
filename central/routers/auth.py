"""
routers/auth.py — Login endpoint + /me.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session

from auth import (
    create_access_token,
    get_current_user,
    verify_db_password,
    verify_password,
)
from config import settings
from models import get_db_user, get_session

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str = "user"


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, session: Session = Depends(get_session)):
    # 1. Check env admin
    if body.username == settings.CT_USERNAME and verify_password(body.password):
        token = create_access_token(body.username)
        return LoginResponse(access_token=token, username=body.username, role="admin")

    # 2. Check DB users
    db_user = get_db_user(session, body.username)
    if db_user and verify_db_password(body.password, db_user.hashed_password):
        token = create_access_token(body.username)
        return LoginResponse(access_token=token, username=body.username, role=db_user.role)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Nieprawidłowa nazwa użytkownika lub hasło.",
    )


@router.get("/me")
async def me(
    user: str = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if user == settings.CT_USERNAME:
        return {"username": user, "role": "admin"}
    db_user = get_db_user(session, user)
    if db_user:
        return {"username": user, "role": db_user.role}
    return {"username": user, "role": "user"}
