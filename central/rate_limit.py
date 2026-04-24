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

# ── Memory protection — cap dictionary sizes to prevent DoS ──────────────────
_MAX_TRACKED_IPS: int   = 10_000
_MAX_TRACKED_USERS: int = 10_000
_IP_LIST_MAX: int       = _IP_MAX * 2      # per-IP list cap
_USER_LIST_MAX: int     = _USER_MAX * 2    # per-user list cap


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
    ip = _client_ip(request)
    if len(_failed_ip) < _MAX_TRACKED_IPS and len(_failed_ip[ip]) < _IP_LIST_MAX:
        _failed_ip[ip].append(time.monotonic())
    if username and username != settings.CT_USERNAME:
        if len(_failed_user) < _MAX_TRACKED_USERS and len(_failed_user[username]) < _USER_LIST_MAX:
            _failed_user[username].append(time.monotonic())


def clear_attempts(request: Request, username: str = "") -> None:
    """Call after a successful authentication to reset counters."""
    _failed_ip.pop(_client_ip(request), None)
    if username:
        _failed_user.pop(username, None)


def purge_expired() -> None:
    """Remove stale entries — call periodically to prevent memory growth."""
    now = time.monotonic()
    for ip in list(_failed_ip.keys()):
        _failed_ip[ip] = [t for t in _failed_ip[ip] if now - t < _IP_WINDOW]
        if not _failed_ip[ip]:
            del _failed_ip[ip]
    for user in list(_failed_user.keys()):
        _failed_user[user] = [t for t in _failed_user[user] if now - t < _USER_WINDOW]
        if not _failed_user[user]:
            del _failed_user[user]


def _client_ip(request: Request) -> str:
    # Use request.client.host as the authoritative IP (set by trusted reverse proxy).
    # X-Forwarded-For is logged for reference only — never trusted for rate limiting
    # because it can be spoofed by the client.
    return request.client.host if request.client else "unknown"
