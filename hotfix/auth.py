"""JWT authentication helpers for DockerMind central."""

import hmac
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
    """Bcrypt hard limit: 72 bytes. Truncate UTF-8 safely."""
    encoded = p.encode("utf-8")
    return encoded[:72].decode("utf-8", errors="ignore") if len(encoded) > 72 else p


_HASHED_PASSWORD: str = pwd_context.hash(_b72(settings.CT_PASSWORD))


def verify_password(plain: str) -> bool:
    return pwd_context.verify(_b72(plain), _HASHED_PASSWORD)


def verify_db_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(_b72(plain), hashed)


def hash_password(plain: str) -> str:
    return pwd_context.hash(_b72(plain))


def validate_password_strength(password: str) -> Optional[str]:
    """Return an error message for a password that does not meet policy."""
    if len(password) < 12:
        return "Hasło musi mieć co najmniej 12 znaków."
    if not re.search(r"[A-Z]", password):
        return "Hasło musi zawierać co najmniej jedną wielką literę."
    if not re.search(r"[0-9]", password):
        return "Hasło musi zawierać co najmniej jedną cyfrę."
    # Avoid the malformed regular expression bundled in the host-access image.
    if not any(not char.isalnum() for char in password):
        return "Hasło musi zawierać co najmniej jeden znak specjalny."
    return None


def create_access_token(username: str, role: str = "user") -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {
        "sub": username,
        "role": role,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.CT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(
            token,
            settings.CT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
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


def get_current_user_info(
    authorization: Optional[str] = Header(default=None),
    dm_token: Optional[str] = Cookie(default=None),
) -> dict:
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
        "role": payload.get("role", "user"),
        "jti": payload.get("jti", ""),
        "exp": payload.get("exp"),
    }


def get_current_user(info: dict = Depends(get_current_user_info)) -> str:
    return info["username"]


def require_agent_token(x_agent_token: Optional[str] = Header(default=None)) -> None:
    if not settings.AGENT_SECRET_TOKEN:
        logger.warning("AGENT_SECRET_TOKEN not set — agent auth disabled.")
        return
    token = (x_agent_token or "").strip()
    if not hmac.compare_digest(token, settings.AGENT_SECRET_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Nieprawidłowy token agenta.",
        )


async def verify_dashboard_ws(websocket: WebSocket) -> Optional[tuple]:
    # Do not log raw URLs, cookies or tokens: query strings may contain secrets.
    path = websocket.scope.get("path", "")
    token = websocket.cookies.get("dm_token") or websocket.query_params.get("token", "")
    if not token:
        logger.warning("DM_AUTH_DIAG_V2 path=%r result=missing_token", path)
        return None
    payload = decode_token(token)
    if not payload:
        logger.warning("DM_AUTH_DIAG_V2 path=%r result=invalid_expired_or_revoked_token", path)
        return None
    username = payload.get("sub")
    role = payload.get("role", "user")
    if not username:
        logger.warning("DM_AUTH_DIAG_V2 path=%r result=missing_username", path)
        return None
    logger.info(
        "DM_AUTH_DIAG_V2 path=%r result=valid_token role=%r cookie_present=%s "
        "agent_id_present=%s target_present=%s terminal_admin=%s",
        path, str(role)[:32], bool(websocket.cookies.get("dm_token")),
        bool(websocket.query_params.get("agent_id")),
        bool(websocket.query_params.get("target", websocket.query_params.get("container", ""))),
        role == "admin",
    )
    return username, role


async def verify_agent_ws(websocket: WebSocket) -> bool:
    token = (
        websocket.headers.get("x-agent-token", "")
        or websocket.query_params.get("agent_token", "")
    ).strip()
    from models import engine as _engine, get_agent_token as _get_agent_token
    from sqlmodel import Session as _Session
    with _Session(_engine) as session:
        db_token = _get_agent_token(session)
    expected = db_token if db_token else settings.AGENT_SECRET_TOKEN
    if not expected:
        logger.warning("AGENT_SECRET_TOKEN not set — agent auth disabled.")
        return True
    ok = hmac.compare_digest(token, expected)
    if not ok:
        logger.warning("Agent token mismatch — token length: %d", len(token))
    return ok
