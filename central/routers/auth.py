"""
routers/auth.py — Login endpoint, returns JWT.
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from auth import create_access_token, verify_password
from config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    if body.username != settings.CT_USERNAME or not verify_password(body.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nieprawidłowa nazwa użytkownika lub hasło.",
        )
    token = create_access_token(body.username)
    return LoginResponse(access_token=token, username=body.username)
