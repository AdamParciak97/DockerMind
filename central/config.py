"""
config.py — Settings loaded from environment / .env file.
"""

import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_INSECURE_SECRET = "insecure-default-change-me-now"
_INSECURE_PASSWORD = "changeme"


class Settings:
    # ── AI ────────────────────────────────────────────────────────────────────
    AI_BASE_URL: str  = os.getenv("AI_BASE_URL", "https://ai.mgmt.pl/llama3/v1")
    AI_MODEL: str     = os.getenv("AI_MODEL", "llama3")
    AI_TIMEOUT: float = float(os.getenv("AI_TIMEOUT", "600"))  # seconds

    # ── Auth ──────────────────────────────────────────────────────────────────
    CT_USERNAME: str   = os.getenv("CT_USERNAME", "admin")
    CT_PASSWORD: str   = os.getenv("CT_PASSWORD", _INSECURE_PASSWORD)
    CT_SECRET_KEY: str = os.getenv("CT_SECRET_KEY", _INSECURE_SECRET)

    # JWT settings
    JWT_ALGORITHM: str      = "HS256"
    JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))  # 8h

    # ── Agent auth ────────────────────────────────────────────────────────────
    AGENT_SECRET_TOKEN: str = os.getenv("AGENT_SECRET_TOKEN", "").strip()

    # ── Server ────────────────────────────────────────────────────────────────
    CT_PORT: int = int(os.getenv("CT_PORT", "8080"))

    # ── Email / SMTP ──────────────────────────────────────────────────────────
    SMTP_HOST: str     = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int     = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str     = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM: str     = os.getenv("SMTP_FROM", "DockerMind")
    SMTP_TLS: bool     = os.getenv("SMTP_TLS", "true").lower() == "true"

    # ── Database ──────────────────────────────────────────────────────────────
    DB_PATH: str = os.getenv("DB_PATH", "/app/data/dockermind.db")
    DATABASE_URL: str = f"sqlite:///{DB_PATH}"

    # ── Exchange / Microsoft Graph ────────────────────────────────────────────
    EXCHANGE_ENABLED:       bool = os.getenv("EXCHANGE_ENABLED", "false").lower() == "true"
    EXCHANGE_TENANT_ID:     str  = os.getenv("EXCHANGE_TENANT_ID", "")
    EXCHANGE_CLIENT_ID:     str  = os.getenv("EXCHANGE_CLIENT_ID", "")
    EXCHANGE_CLIENT_SECRET: str  = os.getenv("EXCHANGE_CLIENT_SECRET", "")
    # Mailbox from which emails are sent (must have Mail.Send permission)
    EXCHANGE_SENDER:        str  = os.getenv("EXCHANGE_SENDER", "")

    # ── LDAP ──────────────────────────────────────────────────────────────────
    LDAP_ENABLED: bool        = os.getenv("LDAP_ENABLED", "false").lower() == "true"
    LDAP_SERVER: str          = os.getenv("LDAP_SERVER", "")
    LDAP_PORT: int            = int(os.getenv("LDAP_PORT", "389"))
    LDAP_USE_SSL: bool        = os.getenv("LDAP_USE_SSL", "false").lower() == "true"
    LDAP_USE_TLS: bool        = os.getenv("LDAP_USE_TLS", "false").lower() == "true"
    LDAP_TLS_VERIFY: bool     = os.getenv("LDAP_TLS_VERIFY", "true").lower() == "true"
    LDAP_BIND_DN: str         = os.getenv("LDAP_BIND_DN", "")
    LDAP_BIND_PASSWORD: str   = os.getenv("LDAP_BIND_PASSWORD", "")
    LDAP_BASE_DN: str         = os.getenv("LDAP_BASE_DN", "")
    # {username} is replaced with the sanitized login name
    LDAP_USER_FILTER: str     = os.getenv("LDAP_USER_FILTER", "(sAMAccountName={username})")
    # DN of the group whose members receive role=admin; empty = everyone is "user"
    LDAP_ADMIN_GROUP_DN: str  = os.getenv("LDAP_ADMIN_GROUP_DN", "")


settings = Settings()


def warn_insecure_defaults() -> None:
    """Log prominent warnings when insecure default credentials are in use."""
    if settings.CT_SECRET_KEY == _INSECURE_SECRET:
        logger.error(
            "SECURITY: CT_SECRET_KEY is the default insecure value! "
            "Set a strong random key in .env (openssl rand -hex 32)."
        )
    if settings.CT_PASSWORD == _INSECURE_PASSWORD:
        logger.error(
            "SECURITY: CT_PASSWORD is 'changeme'! "
            "Set a strong password in .env before exposing this service."
        )
    if not settings.AGENT_SECRET_TOKEN:
        logger.warning(
            "SECURITY: AGENT_SECRET_TOKEN is not set — agent authentication is disabled."
        )
