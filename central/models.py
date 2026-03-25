"""
models.py — SQLModel database models for DockerMind central.
"""

import os
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel, create_engine, Session, select

from config import settings


# ── Models ────────────────────────────────────────────────────────────────────

class Analysis(SQLModel, table=True):
    """Stores a completed AI analysis for a container."""

    id: Optional[int] = Field(default=None, primary_key=True)

    # Which server/container this analysis is about
    agent_id: str = Field(index=True)
    container_name: str = Field(index=True)
    container_image: str = Field(default="")

    # Risk level parsed from AI response: NISKI / ŚREDNI / WYSOKI / KRYTYCZNY
    risk_level: str = Field(default="NIEZNANY")

    # Full AI response text (markdown)
    content: str = Field(default="")

    # Snapshot of key metrics at time of analysis
    cpu_percent: float = Field(default=0.0)
    mem_percent: float = Field(default=0.0)
    restart_count: int = Field(default=0)
    exit_code: int = Field(default=0)
    last_crash: Optional[str] = Field(default=None)

    # Timestamps
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ContainerEvent(SQLModel, table=True):
    """Records crash/restart events for the history timeline and charts."""

    id: Optional[int] = Field(default=None, primary_key=True)

    agent_id: str = Field(index=True)
    container_name: str = Field(index=True)

    # "restart" | "crash" | "stop" | "start"
    event_type: str = Field(default="restart")

    exit_code: int = Field(default=0)
    restart_count: int = Field(default=0)

    # Snapshot metrics at event time
    cpu_percent: float = Field(default=0.0)
    mem_percent: float = Field(default=0.0)

    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


# ── Engine + session ──────────────────────────────────────────────────────────

def _ensure_data_dir() -> None:
    db_dir = os.path.dirname(settings.DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)


_ensure_data_dir()

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)


def create_db() -> None:
    """Create all tables. Call once on startup."""
    SQLModel.metadata.create_all(engine)


def get_session():
    """FastAPI dependency: yields a DB session."""
    with Session(engine) as session:
        yield session


# ── Query helpers ─────────────────────────────────────────────────────────────

def get_analyses(
    session: Session,
    agent_id: Optional[str] = None,
    container_name: Optional[str] = None,
    limit: int = 50,
) -> list[Analysis]:
    stmt = select(Analysis)
    if agent_id:
        stmt = stmt.where(Analysis.agent_id == agent_id)
    if container_name:
        stmt = stmt.where(Analysis.container_name == container_name)
    stmt = stmt.order_by(Analysis.created_at.desc()).limit(limit)
    return list(session.exec(stmt))


def get_analysis(session: Session, analysis_id: int) -> Optional[Analysis]:
    return session.get(Analysis, analysis_id)


def delete_analysis(session: Session, analysis_id: int) -> bool:
    obj = session.get(Analysis, analysis_id)
    if not obj:
        return False
    session.delete(obj)
    session.commit()
    return True


def save_analysis(session: Session, analysis: Analysis) -> Analysis:
    session.add(analysis)
    session.commit()
    session.refresh(analysis)
    return analysis


def get_events(
    session: Session,
    agent_id: str,
    container_name: str,
    days: int = 7,
) -> list[ContainerEvent]:
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = (
        select(ContainerEvent)
        .where(ContainerEvent.agent_id == agent_id)
        .where(ContainerEvent.container_name == container_name)
        .where(ContainerEvent.occurred_at >= cutoff)
        .order_by(ContainerEvent.occurred_at.asc())
    )
    return list(session.exec(stmt))


def record_event(
    session: Session,
    agent_id: str,
    container_name: str,
    event_type: str,
    **kwargs,
) -> ContainerEvent:
    evt = ContainerEvent(
        agent_id=agent_id,
        container_name=container_name,
        event_type=event_type,
        **kwargs,
    )
    session.add(evt)
    session.commit()
    session.refresh(evt)
    return evt
