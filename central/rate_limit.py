"""
rate_limit.py — Login protection: IP sliding-window + per-username lockout.

IP rate limit:   max 10 failed attempts per 5 minutes per client IP.
Username lockout: max 15 failed attempts per 15 minutes per username.
                  Env admin (CT_USERNAME) is never locked (DoS protection).
"""

import time
from collections import defaultdict

from fastapi import HTTPException, Request

from config import settings

# ── IP sliding-window ─────────────────────────────────────────────────────────

_IP_WINDOW: int = 300       # 5 minutes
_IP_MAX: int    = 10

_failed_ip: dict[str, list[float]] = defaultdict(list)

# ── Username lockout ──────────────────────────────────────────────────────────

_USER_WINDOW: int = 900     # 15 minutes
_USER_MAX: int    = 15

_failed_user: dict[str, list[float]] = defaultdict(list)


# ── Public API ────────────────────────────────────────────────────────────────

def check_login_rate_limit(request: Request) -> None:
    """Raise 429 if client IP exceeded the failed-attempt limit."""
    ip = _client_ip(request)
    now = time.monotonic()
    _failed_ip[ip] = [t for t in _failed_ip[ip] if now - t < _IP_WINDOW]
    if len(_failed_ip[ip]) >= _IP_MAX:
        raise HTTPException(
            status_code=429,
            detail=f"Zbyt wiele nieudanych prób logowania. Spróbuj za {_IP_WINDOW // 60} minut.",
        )


def check_username_lockout(username: str) -> None:
    """Raise 429 if username exceeded the failed-attempt limit. Never locks env admin."""
    if username == settings.CT_USERNAME:
        return
    now = time.monotonic()
    _failed_user[username] = [t for t in _failed_user[username] if now - t < _USER_WINDOW]
    if len(_failed_user[username]) >= _USER_MAX:
        raise HTTPException(
            status_code=429,
            detail=f"Konto tymczasowo zablokowane. Spróbuj za {_USER_WINDOW // 60} minut.",
        )


def record_failed_attempt(request: Request, username: str = "") -> None:
    """Call after a failed authentication attempt."""
    _failed_ip[_client_ip(request)].append(time.monotonic())
    if username and username != settings.CT_USERNAME:
        _failed_user[username].append(time.monotonic())


def clear_attempts(request: Request, username: str = "") -> None:
    """Call after a successful authentication to reset counters."""
    _failed_ip.pop(_client_ip(request), None)
    if username:
        _failed_user.pop(username, None)


def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
