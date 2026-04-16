"""
routers/auth.py — Login, logout, /me.

Authentication priority:
  1. Env admin (CT_USERNAME / CT_PASSWORD)
  2. DB users (hashed password in SQLite)
  3. LDAP / Active Directory (when LDAP_ENABLED=true)

Session token stored in httpOnly cookie "dm_token".
Bearer header accepted as fallback for API clients.
"""

import secrets
from datetime import datetime, timedelta, timezone

import jwt as _jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlmodel import Session

from auth import (
    create_access_token,
    decode_token,
    get_current_user_info,
    verify_db_password,
    verify_password,
)
from config import settings
from ldap_auth import ldap_authenticate
from models import (
    create_session,
    delete_session,
    get_db_user,
    get_session,
    is_token_revoked,
    log_audit,
    revoke_token,
)
from rate_limit import (
    check_login_rate_limit,
    check_username_lockout,
    clear_attempts,
    record_failed_attempt,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_COOKIE_NAME = "dm_token"
_COOKIE_OPTS = dict(
    key=_COOKIE_NAME,
    httponly=True,
    samesite="lax",
    secure=True,      # Only sent over HTTPS (nginx handles TLS)
    path="/",
)


def _set_token_cookie(response: Response, token: str) -> None:
    response.set_cookie(value=token, max_age=settings.JWT_EXPIRE_MINUTES * 60, **_COOKIE_OPTS)


def _clear_token_cookie(response: Response) -> None:
    response.delete_cookie(_COOKIE_NAME, path="/", samesite="lax")


def _client_ip(request: Request) -> str:
    ff = request.headers.get("X-Forwarded-For", "")
    return ff.split(",")[0].strip() if ff else (request.client.host if request.client else "")


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    ip = _client_ip(request)

    # ── Rate limits ────────────────────────────────────────────────────────────
    check_login_rate_limit(request)
    check_username_lockout(body.username)

    role: str | None = None

    # ── 1. Env admin ───────────────────────────────────────────────────────────
    if body.username == settings.CT_USERNAME and verify_password(body.password):
        role = "admin"

    # ── 2. DB users ────────────────────────────────────────────────────────────
    if role is None:
        db_user = get_db_user(session, body.username)
        if db_user and db_user.hashed_password and verify_db_password(body.password, db_user.hashed_password):
            role = db_user.role

    # ── 3. LDAP ────────────────────────────────────────────────────────────────
    if role is None:
        ldap_role = ldap_authenticate(body.username, body.password)
        if ldap_role is not None:
            ldap_db = get_db_user(session, body.username)
            role = ldap_db.role if ldap_db else ldap_role

    # ── Auth failed ────────────────────────────────────────────────────────────
    if role is None:
        record_failed_attempt(request, body.username)
        log_audit(session, "login_failed", username=body.username, ip=ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nieprawidłowa nazwa użytkownika lub hasło.",
        )

    # ── Success ────────────────────────────────────────────────────────────────
    clear_attempts(request, body.username)
    token = create_access_token(body.username, role=role)
    _set_token_cookie(response, token)

    # Register active session
    raw = _jwt.decode(token, settings.CT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    jti = raw.get("jti", "")
    exp = raw.get("exp")
    if jti and exp:
        expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
        create_session(
            session,
            jti=jti,
            username=body.username,
            ip=ip,
            user_agent=request.headers.get("user-agent", "")[:256],
            expires_at=expires_at,
        )

    # Set CSRF token cookie (JS-readable, double-submit pattern)
    response.set_cookie(
        key="csrf_token",
        value=secrets.token_hex(32),
        httponly=False,
        samesite="lax",
        secure=True,
        path="/",
        max_age=settings.JWT_EXPIRE_MINUTES * 60,
    )

    log_audit(session, "login_success", username=body.username, ip=ip)

    return {"username": body.username, "role": role}


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    """Revoke the current JWT, remove active session and clear the cookie."""
    jti = info.get("jti", "")
    exp = info.get("exp")
    if jti and exp:
        expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
        # delete_session also adds to RevokedToken, so we don't call revoke_token separately
        if not delete_session(session, jti, expires_at):
            revoke_token(session, jti, expires_at)  # fallback if session not found
    _clear_token_cookie(response)
    response.delete_cookie("csrf_token", path="/", samesite="lax")
    log_audit(session, "logout", username=info["username"], ip=_client_ip(request))


@router.get("/me")
async def me(
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    user = info["username"]
    # Env admin
    if user == settings.CT_USERNAME:
        return {"username": user, "role": "admin"}
    # DB user (includes LDAP stubs with role override)
    db_user = get_db_user(session, user)
    if db_user:
        return {"username": user, "role": db_user.role}
    # LDAP user without DB entry — role from JWT
    return {"username": user, "role": info["role"]}
