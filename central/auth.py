"""
auth.py — JWT authentication for DockerMind central.

Multi-user model:
 - env admin (CT_USERNAME / CT_PASSWORD) always works
 - additional users stored in DB (User table)
Agents authenticate via AGENT_SECRET_TOKEN header.

Session token: httpOnly cookie "dm_token" (preferred) OR Bearer header (fallback).
"""

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Cookie, Depends, Header, HTTPException, WebSocket, status
from passlib.context import CryptContext

from config import settings

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _b72(p: str) -> str:
    """Bcrypt hard limit: 72 bytes. Truncate UTF-8 encoded password safely."""
    encoded = p.encode("utf-8")
    return encoded[:72].decode("utf-8", errors="ignore") if len(encoded) > 72 else p


# Pre-hash the configured password once at import time
_HASHED_PASSWORD: str = pwd_context.hash(_b72(settings.CT_PASSWORD))


# ── Password helpers ──────────────────────────────────────────────────────────

def verify_password(plain: str) -> bool:
    """Verify against the env-configured admin password."""
    return pwd_context.verify(_b72(plain), _HASHED_PASSWORD)


def verify_db_password(plain: str, hashed: str) -> bool:
    """Verify against a DB-stored hashed password."""
    return pwd_context.verify(_b72(plain), hashed)


def hash_password(plain: str) -> str:
    return pwd_context.hash(_b72(plain))


def validate_password_strength(password: str) -> Optional[str]:
    """
    Returns error message if password is too weak, None if OK.
    Rules: min 8 chars, at least one uppercase letter, at least one digit.
    """
    if len(password) < 8:
        return "Hasło musi mieć co najmniej 8 znaków."
    if not re.search(r"[A-Z]", password):
        return "Hasło musi zawierać co najmniej jedną wielką literę."
    if not re.search(r"[0-9]", password):
        return "Hasło musi zawierać co najmniej jedną cyfrę."
    return None


# ── JWT ───────────────────────────────────────────────────────────────────────

def create_access_token(username: str, role: str = "user") -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {
        "sub":  username,
        "role": role,
        "exp":  expire,
        "iat":  datetime.now(timezone.utc),
        "jti":  str(uuid.uuid4()),   # unique token ID — used for revocation
    }
    return jwt.encode(payload, settings.CT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(
            token,
            settings.CT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        # Check revocation list
        jti = payload.get("jti")
        if jti:
            from models import is_token_revoked
            if is_token_revoked(jti):
                logger.debug("JWT %s is revoked.", jti[:8])
                return None
        return payload
    except jwt.ExpiredSignatureError:
        logger.debug("JWT token expired.")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug("JWT invalid: %s", e)
        return None


# ── FastAPI dependencies ───────────────────────────────────────────────────────

def get_current_user_info(
    authorization: Optional[str] = Header(default=None),
    dm_token: Optional[str] = Cookie(default=None),
) -> dict:
    """
    Reads session from httpOnly cookie "dm_token" first, then Bearer header.
    Returns {"username": str, "role": str}.
    """
    token = dm_token
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nie uwierzytelniony.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token nieważny lub wygasł.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {
        "username": payload.get("sub", "unknown"),
        "role":     payload.get("role", "user"),
        "jti":      payload.get("jti", ""),
        "exp":      payload.get("exp"),
    }


def get_current_user(info: dict = Depends(get_current_user_info)) -> str:
    """Backward-compat dependency — returns username string."""
    return info["username"]


def require_agent_token(
    x_agent_token: Optional[str] = Header(default=None),
) -> None:
    """
    Dependency for agent WebSocket endpoint.
    Raises 403 if token doesn't match AGENT_SECRET_TOKEN.
    """
    if not settings.AGENT_SECRET_TOKEN:
        logger.warning("AGENT_SECRET_TOKEN not set — agent auth disabled.")
        return
    token = (x_agent_token or "").strip()
    if token != settings.AGENT_SECRET_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Nieprawidłowy token agenta.",
        )


async def verify_dashboard_ws(websocket: WebSocket) -> Optional[tuple]:
    """
    Validate session for dashboard/terminal WebSocket connections.
    Checks httpOnly cookie first, then ?token= query param (fallback).
    Returns (username, role) or None.
    """
    token = websocket.cookies.get("dm_token") or websocket.query_params.get("token", "")
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    username = payload.get("sub")
    role = payload.get("role", "user")
    if not username:
        return None
    return (username, role)


async def verify_agent_ws(websocket: WebSocket) -> bool:
    """
    Validate agent token for agent WebSocket connections.
    DB-stored token (from rotation) takes priority over AGENT_SECRET_TOKEN env var.
    Token passed as header X-Agent-Token or query param agent_token.
    Returns True if valid (or auth disabled).
    """
    token = (
        websocket.headers.get("x-agent-token", "")
        or websocket.query_params.get("agent_token", "")
    ).strip()

    # Resolve expected token: DB first, then env
    from models import engine as _engine, get_agent_token as _get_agent_token
    from sqlmodel import Session as _Session
    with _Session(_engine) as _sess:
        db_token = _get_agent_token(_sess)

    expected = db_token if db_token else settings.AGENT_SECRET_TOKEN

    if not expected:
        logger.warning("AGENT_SECRET_TOKEN not set — agent auth disabled.")
        return True

    ok = token == expected
    if not ok:
        logger.warning(
            "Agent token mismatch — received=%r (len=%d)",
            token[:6] + "..." if token else "(empty)", len(token),
        )
    return ok
