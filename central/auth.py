"""
auth.py — JWT authentication for DockerMind central.

Multi-user model:
 - env admin (CT_USERNAME / CT_PASSWORD) always works
 - additional users stored in DB (User table)
Agents authenticate via AGENT_SECRET_TOKEN header.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, Header, HTTPException, WebSocket, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from sqlmodel import Session

from config import settings

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


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


# ── JWT ───────────────────────────────────────────────────────────────────────

def create_access_token(username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {
        "sub": username,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.CT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(
            token,
            settings.CT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError:
        logger.debug("JWT token expired.")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug("JWT invalid: %s", e)
        return None


# ── FastAPI dependencies ───────────────────────────────────────────────────────

def get_current_user(token: str = Depends(oauth2_scheme)) -> str:
    """
    Dependency for protected REST endpoints.
    Returns username string or raises 401.
    """
    payload = decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token nieważny lub wygasł.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload.get("sub", "unknown")


def require_agent_token(x_agent_token: str = Header(default="")) -> None:
    """
    Dependency for agent WebSocket endpoint.
    Raises 403 if token doesn't match AGENT_SECRET_TOKEN.
    """
    if not settings.AGENT_SECRET_TOKEN:
        # If token not configured, allow all (dev mode)
        logger.warning("AGENT_SECRET_TOKEN not set — agent auth disabled.")
        return
    if x_agent_token != settings.AGENT_SECRET_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Nieprawidłowy token agenta.",
        )


async def verify_dashboard_ws(websocket: WebSocket) -> Optional[str]:
    """
    Validate JWT for dashboard WebSocket connections.
    Token passed as query param: /ws/dashboard?token=<jwt>
    Returns username or None.
    """
    token = websocket.query_params.get("token", "")
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    return payload.get("sub")


async def verify_agent_ws(websocket: WebSocket) -> bool:
    """
    Validate agent token for agent WebSocket connections.
    Token passed as header X-Agent-Token or query param agent_token.
    Returns True if valid (or auth disabled).
    """
    if not settings.AGENT_SECRET_TOKEN:
        logger.warning("AGENT_SECRET_TOKEN not set — agent auth disabled.")
        return True
    token = (
        websocket.headers.get("x-agent-token", "")
        or websocket.query_params.get("agent_token", "")
    ).strip()
    ok = token == settings.AGENT_SECRET_TOKEN
    if not ok:
        logger.warning(
            "Agent token mismatch — received=%r (len=%d) expected_len=%d",
            token[:6] + "...", len(token), len(settings.AGENT_SECRET_TOKEN),
        )
    return ok
