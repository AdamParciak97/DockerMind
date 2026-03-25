"""
config.py — Settings loaded from environment / .env file.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # ── AI ────────────────────────────────────────────────────────────────────
    AI_BASE_URL: str  = os.getenv("AI_BASE_URL", "https://ai.mgmt.pl/llama3/v1")
    AI_MODEL: str     = os.getenv("AI_MODEL", "llama3")
    AI_TIMEOUT: float = float(os.getenv("AI_TIMEOUT", "600"))  # seconds

    # ── Auth ──────────────────────────────────────────────────────────────────
    CT_USERNAME: str   = os.getenv("CT_USERNAME", "admin")
    CT_PASSWORD: str   = os.getenv("CT_PASSWORD", "changeme")
    CT_SECRET_KEY: str = os.getenv("CT_SECRET_KEY", "insecure-default-change-me-now")

    # JWT settings
    JWT_ALGORITHM: str      = "HS256"
    JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))  # 8h

    # ── Agent auth ────────────────────────────────────────────────────────────
    AGENT_SECRET_TOKEN: str = os.getenv("AGENT_SECRET_TOKEN", "").strip()

    # ── Server ────────────────────────────────────────────────────────────────
    CT_PORT: int = int(os.getenv("CT_PORT", "8080"))

    # ── Database ──────────────────────────────────────────────────────────────
    DB_PATH: str = os.getenv("DB_PATH", "/app/data/dockermind.db")
    DATABASE_URL: str = f"sqlite:///{DB_PATH}"


settings = Settings()
