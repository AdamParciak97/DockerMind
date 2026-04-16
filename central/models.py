"""
models.py — SQLModel database models for DockerMind central.
"""

import base64
import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlmodel import Field, Session, SQLModel, create_engine, select

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


class MetricSnapshot(SQLModel, table=True):
    """Periodic CPU/RAM/network/blkio snapshot for a container (every ~30 s)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: str = Field(index=True)
    container_name: str = Field(index=True)
    recorded_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), index=True
    )
    cpu_percent: float = Field(default=0.0)
    mem_percent: float = Field(default=0.0)
    mem_usage_bytes: int = Field(default=0)
    net_rx_bytes: int = Field(default=0)
    net_tx_bytes: int = Field(default=0)
    blkio_read_bytes: int = Field(default=0)
    blkio_write_bytes: int = Field(default=0)
    pids: int = Field(default=0)
    status: str = Field(default="unknown")


class AlertRule(SQLModel, table=True):
    """User-defined alert rule for a container metric."""

    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: str = Field(index=True)
    container_name: str = Field(index=True)   # "*" = all containers on this agent
    metric: str                                # cpu_percent | mem_percent | restart_count | status_stopped
    threshold: float = Field(default=80.0)
    min_duration: int = Field(default=0)       # minutes; 0 = fire immediately, N = sustained N min
    enabled: bool = Field(default=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class AlertEvent(SQLModel, table=True):
    """A triggered alert instance."""

    id: Optional[int] = Field(default=None, primary_key=True)
    rule_id: int = Field(index=True)
    agent_id: str = Field(index=True)
    container_name: str = Field(index=True)
    metric: str
    value: float
    threshold: float
    status: str = Field(default="active")     # active | acknowledged | resolved
    triggered_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    ack_at: Optional[datetime] = Field(default=None)


class Secret(SQLModel, table=True):
    """Encrypted key-value secret stored in the local DB."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    encrypted_value: str = Field(default="")
    description: str = Field(default="")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class User(SQLModel, table=True):
    """DB-managed user (env admin is separate)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    hashed_password: str = Field(default="")
    role: str = Field(default="user")  # "admin" | "user"
    source: str = Field(default="db")  # "db" | "ldap"
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ServerGroup(SQLModel, table=True):
    """Named group of servers (agents)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    color: str = Field(default="#3b82f6")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ServerGroupMember(SQLModel, table=True):
    """Maps agent_id → ServerGroup."""

    id: Optional[int] = Field(default=None, primary_key=True)
    group_id: int = Field(index=True)
    agent_id: str = Field(index=True)


class LdapConfig(SQLModel, table=True):
    """Singleton LDAP configuration stored in DB (id always 1)."""

    id: int = Field(default=1, primary_key=True)
    enabled: bool = Field(default=False)
    server: str = Field(default="")
    port: int = Field(default=389)
    use_ssl: bool = Field(default=False)
    use_tls: bool = Field(default=False)
    tls_verify: bool = Field(default=True)
    bind_dn: str = Field(default="")
    bind_password_enc: str = Field(default="")   # encrypted via encrypt_secret()
    base_dn: str = Field(default="")
    user_filter: str = Field(default="(sAMAccountName={username})")
    admin_group_dn: str = Field(default="")
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class UserGroup(SQLModel, table=True):
    """Named group of users."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class UserGroupMember(SQLModel, table=True):
    """Maps username → UserGroup."""

    id: Optional[int] = Field(default=None, primary_key=True)
    group_id: int = Field(index=True)
    username: str


class UserGroupServerGroup(SQLModel, table=True):
    """Maps UserGroup → ServerGroup (which server groups a user group can see)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_group_id: int = Field(index=True)
    server_group_id: int = Field(index=True)


class RevokedToken(SQLModel, table=True):
    """JWT blacklist — stores revoked token IDs until they expire naturally."""

    jti: str = Field(primary_key=True)         # JWT "jti" claim (UUID)
    expires_at: datetime                        # when the token expires (for cleanup)
    revoked_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class AuditLog(SQLModel, table=True):
    """Immutable audit trail for security-relevant actions."""

    id: Optional[int] = Field(default=None, primary_key=True)
    action: str = Field(index=True)            # e.g. "login_success", "container_action"
    username: str = Field(default="", index=True)
    ip: str = Field(default="")
    detail: str = Field(default="")            # JSON-encoded extra context
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), index=True
    )


class ActiveSession(SQLModel, table=True):
    """Tracks currently active user sessions (one row per valid JWT)."""

    jti: str = Field(primary_key=True)
    username: str = Field(index=True)
    ip: str = Field(default="")
    user_agent: str = Field(default="")       # truncated to 256 chars
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    expires_at: datetime = Field(index=True)


class AgentToken(SQLModel, table=True):
    """Stores the current agent secret token (singleton, id=1).
    When present, takes priority over AGENT_SECRET_TOKEN env variable.
    """

    id: int = Field(default=1, primary_key=True)
    token_enc: str = Field(default="")         # Fernet-encrypted token
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    created_by: str = Field(default="")


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
    """Create all tables, apply column migrations, migrate encryption."""
    SQLModel.metadata.create_all(engine)
    _migrate_db()
    with Session(engine) as session:
        migrate_secrets_to_fernet(session)


def _migrate_db() -> None:
    """Add missing columns introduced after initial release (SQLite ALTER TABLE)."""
    import sqlite3
    db_path = settings.DB_PATH
    if not os.path.exists(db_path):
        return
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    # alertrule.min_duration — added in v1.1
    _add_column_if_missing(cur, "alertrule", "min_duration", "INTEGER NOT NULL DEFAULT 0")
    # user.source — added in v1.2 (distinguish db vs ldap stub users)
    _add_column_if_missing(cur, "user", "source", "TEXT NOT NULL DEFAULT 'db'")
    con.commit()
    con.close()


def _add_column_if_missing(cur, table: str, column: str, col_def: str) -> None:
    try:
        cur.execute(f"SELECT {column} FROM {table} LIMIT 1")
    except Exception:
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Migration skipped (%s.%s): %s", table, column, e)


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


# ── Metric snapshots ──────────────────────────────────────────────────────────

def get_metric_snapshots(
    session: Session,
    agent_id: str,
    container_name: str,
    hours: int = 24,
) -> list[MetricSnapshot]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    stmt = (
        select(MetricSnapshot)
        .where(MetricSnapshot.agent_id == agent_id)
        .where(MetricSnapshot.container_name == container_name)
        .where(MetricSnapshot.recorded_at >= cutoff)
        .order_by(MetricSnapshot.recorded_at.asc())
    )
    return list(session.exec(stmt))


def cleanup_old_snapshots(session: Session, days: int = 7) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    old = session.exec(
        select(MetricSnapshot).where(MetricSnapshot.recorded_at < cutoff)
    ).all()
    for s in old:
        session.delete(s)
    if old:
        session.commit()


# ── Alert rules & events ──────────────────────────────────────────────────────

def get_alert_rules(
    session: Session,
    agent_id: Optional[str] = None,
    container_name: Optional[str] = None,
) -> list[AlertRule]:
    stmt = select(AlertRule)
    if agent_id:
        stmt = stmt.where(AlertRule.agent_id == agent_id)
    if container_name:
        stmt = stmt.where(AlertRule.container_name == container_name)
    stmt = stmt.order_by(AlertRule.created_at.desc())
    return list(session.exec(stmt))


def get_alert_events(
    session: Session,
    agent_id: Optional[str] = None,
    container_name: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
) -> list[AlertEvent]:
    stmt = select(AlertEvent)
    if agent_id:
        stmt = stmt.where(AlertEvent.agent_id == agent_id)
    if container_name:
        stmt = stmt.where(AlertEvent.container_name == container_name)
    if status:
        stmt = stmt.where(AlertEvent.status == status)
    stmt = stmt.order_by(AlertEvent.triggered_at.desc()).limit(limit)
    return list(session.exec(stmt))


def _metric_value(container: dict, metric: str) -> float:
    if metric == "cpu_percent":
        return float(container.get("cpu_percent", 0))
    if metric == "mem_percent":
        return float(container.get("memory", {}).get("percent", 0))
    if metric == "restart_count":
        return float(container.get("restart_count", 0))
    if metric == "status_stopped":
        return 0.0 if container.get("status") in ("running", "restarting") else 1.0
    return 0.0


def _metric_value_from_snapshot(snap: "MetricSnapshot", metric: str) -> float:
    if metric == "cpu_percent":   return snap.cpu_percent
    if metric == "mem_percent":   return snap.mem_percent
    if metric == "status_stopped": return 0.0 if snap.status in ("running", "restarting") else 1.0
    return 0.0


def process_agent_data(
    session: Session,
    agent_id: str,
    containers: list[dict],
) -> list[dict]:
    """Save metric snapshots and evaluate alert rules. Returns new alert dicts."""
    now = datetime.now(timezone.utc)

    # Save snapshots for every container (running or not)
    for c in containers:
        snap = MetricSnapshot(
            agent_id=agent_id,
            container_name=c.get("name", ""),
            recorded_at=now,
            cpu_percent=c.get("cpu_percent", 0.0),
            mem_percent=c.get("memory", {}).get("percent", 0.0),
            mem_usage_bytes=c.get("memory", {}).get("usage_bytes", 0),
            net_rx_bytes=c.get("network", {}).get("rx_bytes", 0),
            net_tx_bytes=c.get("network", {}).get("tx_bytes", 0),
            blkio_read_bytes=c.get("blkio", {}).get("read_bytes", 0),
            blkio_write_bytes=c.get("blkio", {}).get("write_bytes", 0),
            pids=c.get("pids", 0),
            status=c.get("status", "unknown"),
        )
        session.add(snap)

    # Evaluate alert rules
    rules = session.exec(
        select(AlertRule)
        .where(AlertRule.agent_id == agent_id)
        .where(AlertRule.enabled == True)  # noqa: E712
    ).all()

    new_alerts: list[dict] = []
    for rule in rules:
        targets = [
            c for c in containers
            if rule.container_name in ("*", c.get("name", ""))
        ]
        for c in targets:
            value = _metric_value(c, rule.metric)

            if rule.min_duration > 0:
                # Sustained alert: check all snapshots in the last min_duration minutes
                cutoff = now - timedelta(minutes=rule.min_duration)
                recent = list(session.exec(
                    select(MetricSnapshot)
                    .where(MetricSnapshot.agent_id == agent_id)
                    .where(MetricSnapshot.container_name == c.get("name", ""))
                    .where(MetricSnapshot.recorded_at >= cutoff)
                ).all())
                # Need at least (min_duration * 2) data points (~30s interval)
                min_points = max(2, rule.min_duration * 2)
                fired = (
                    len(recent) >= min_points
                    and all(_metric_value_from_snapshot(s, rule.metric) >= rule.threshold for s in recent)
                )
            else:
                fired = value >= rule.threshold

            existing = session.exec(
                select(AlertEvent)
                .where(AlertEvent.rule_id == rule.id)
                .where(AlertEvent.container_name == c.get("name", ""))
                .where(AlertEvent.status == "active")
            ).first()

            if fired and not existing:
                evt = AlertEvent(
                    rule_id=rule.id,
                    agent_id=agent_id,
                    container_name=c.get("name", ""),
                    metric=rule.metric,
                    value=value,
                    threshold=rule.threshold,
                )
                session.add(evt)
                new_alerts.append({
                    "agent_id": agent_id,
                    "container_name": c.get("name", ""),
                    "metric": rule.metric,
                    "value": value,
                    "threshold": rule.threshold,
                })
            elif not fired and existing:
                existing.status = "resolved"
                session.add(existing)

    session.commit()
    return new_alerts


# ── Secrets — AES-256 (Fernet) with XOR migration ────────────────────────────

def _fernet() -> Fernet:
    """Return a Fernet instance keyed on CT_SECRET_KEY (SHA-256, then base64)."""
    raw_key = hashlib.sha256(settings.CT_SECRET_KEY.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(raw_key)
    return Fernet(fernet_key)


def encrypt_secret(value: str) -> str:
    """Encrypt with AES-256 (Fernet). Returns a token string."""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(enc: str) -> str:
    """
    Decrypt a secret value.
    Tries Fernet (new format) first; falls back to legacy XOR for migration.
    """
    if not enc:
        return ""
    # Fernet tokens start with 'gA' (version byte 0x80 in URL-safe base64)
    if enc.startswith("gA"):
        return _fernet().decrypt(enc.encode("ascii")).decode("utf-8")
    # Legacy XOR fallback
    return _xor_decrypt(enc)


def _xor_encrypt(value: str) -> str:
    key = hashlib.sha256(settings.CT_SECRET_KEY.encode()).digest()
    data = value.encode("utf-8")
    encrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return base64.urlsafe_b64encode(encrypted).decode()


def _xor_decrypt(enc: str) -> str:
    key = hashlib.sha256(settings.CT_SECRET_KEY.encode()).digest()
    data = base64.urlsafe_b64decode(enc.encode())
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data)).decode("utf-8")


def migrate_secrets_to_fernet(session: Session) -> None:
    """Re-encrypt any XOR-encoded secrets and LDAP password to Fernet on startup."""
    import logging
    log = logging.getLogger(__name__)
    changed = 0

    # Migrate Secret table
    for sec in session.exec(select(Secret)).all():
        if sec.encrypted_value and not sec.encrypted_value.startswith("gA"):
            try:
                plain = _xor_decrypt(sec.encrypted_value)
                sec.encrypted_value = encrypt_secret(plain)
                session.add(sec)
                changed += 1
            except Exception as e:
                log.warning("Skipping secret %s migration: %s", sec.id, e)

    # Migrate LdapConfig.bind_password_enc
    cfg = session.get(LdapConfig, 1)
    if cfg and cfg.bind_password_enc and not cfg.bind_password_enc.startswith("gA"):
        try:
            plain = _xor_decrypt(cfg.bind_password_enc)
            cfg.bind_password_enc = encrypt_secret(plain)
            session.add(cfg)
            changed += 1
        except Exception as e:
            log.warning("Skipping LDAP password migration: %s", e)

    if changed:
        session.commit()
        log.info("Migrated %d secret(s) from XOR to AES-256 (Fernet).", changed)


def get_secrets(session: Session) -> list[Secret]:
    return list(session.exec(select(Secret).order_by(Secret.name)).all())


def get_secret(session: Session, secret_id: int) -> Optional[Secret]:
    return session.get(Secret, secret_id)


def delete_secret(session: Session, secret_id: int) -> bool:
    obj = session.get(Secret, secret_id)
    if not obj:
        return False
    session.delete(obj)
    session.commit()
    return True


# ── Users ─────────────────────────────────────────────────────────────────────

def get_db_user(session: Session, username: str) -> Optional[User]:
    return session.exec(select(User).where(User.username == username)).first()


def get_all_users(session: Session) -> list[User]:
    return list(session.exec(select(User).order_by(User.username)).all())


def create_db_user(session: Session, username: str, hashed_password: str, role: str = "user", source: str = "db") -> User:
    u = User(username=username, hashed_password=hashed_password, role=role, source=source)
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def delete_db_user(session: Session, username: str) -> bool:
    u = session.exec(select(User).where(User.username == username)).first()
    if not u:
        return False
    session.delete(u)
    session.commit()
    return True


def update_db_user_password(session: Session, username: str, hashed_password: str) -> bool:
    u = session.exec(select(User).where(User.username == username)).first()
    if not u:
        return False
    u.hashed_password = hashed_password
    session.add(u)
    session.commit()
    return True


# ── Server groups ─────────────────────────────────────────────────────────────

def get_server_groups(session: Session) -> list[dict]:
    groups = list(session.exec(select(ServerGroup).order_by(ServerGroup.name)).all())
    result = []
    for g in groups:
        members = list(session.exec(
            select(ServerGroupMember).where(ServerGroupMember.group_id == g.id)
        ).all())
        result.append({
            "id": g.id, "name": g.name, "color": g.color,
            "created_at": g.created_at.isoformat(),
            "members": [m.agent_id for m in members],
        })
    return result


def create_server_group(session: Session, name: str, color: str = "#3b82f6") -> dict:
    g = ServerGroup(name=name, color=color)
    session.add(g)
    session.commit()
    session.refresh(g)
    return {"id": g.id, "name": g.name, "color": g.color,
            "created_at": g.created_at.isoformat(), "members": []}


def delete_server_group(session: Session, group_id: int) -> bool:
    g = session.get(ServerGroup, group_id)
    if not g:
        return False
    # Remove members
    members = session.exec(
        select(ServerGroupMember).where(ServerGroupMember.group_id == group_id)
    ).all()
    for m in members:
        session.delete(m)
    session.delete(g)
    session.commit()
    return True


def set_server_group_members(session: Session, group_id: int, agent_ids: list[str]) -> bool:
    g = session.get(ServerGroup, group_id)
    if not g:
        return False
    old = session.exec(
        select(ServerGroupMember).where(ServerGroupMember.group_id == group_id)
    ).all()
    for m in old:
        session.delete(m)
    for aid in agent_ids:
        session.add(ServerGroupMember(group_id=group_id, agent_id=aid))
    session.commit()
    return True


# ── User groups ───────────────────────────────────────────────────────────────

def get_user_groups(session: Session) -> list[dict]:
    groups = list(session.exec(select(UserGroup).order_by(UserGroup.name)).all())
    result = []
    for g in groups:
        members = list(session.exec(
            select(UserGroupMember).where(UserGroupMember.group_id == g.id)
        ).all())
        sg_rows = list(session.exec(
            select(UserGroupServerGroup).where(UserGroupServerGroup.user_group_id == g.id)
        ).all())
        result.append({
            "id": g.id, "name": g.name,
            "created_at": g.created_at.isoformat(),
            "members": [m.username for m in members],
            "server_group_ids": [r.server_group_id for r in sg_rows],
        })
    return result


def create_user_group(session: Session, name: str) -> dict:
    g = UserGroup(name=name)
    session.add(g)
    session.commit()
    session.refresh(g)
    return {"id": g.id, "name": g.name, "created_at": g.created_at.isoformat(), "members": []}


def delete_user_group(session: Session, group_id: int) -> bool:
    g = session.get(UserGroup, group_id)
    if not g:
        return False
    members = session.exec(
        select(UserGroupMember).where(UserGroupMember.group_id == group_id)
    ).all()
    for m in members:
        session.delete(m)
    session.delete(g)
    session.commit()
    return True


def get_ldap_config(session: Session) -> Optional["LdapConfig"]:
    return session.get(LdapConfig, 1)


def save_ldap_config(session: Session, data: dict) -> "LdapConfig":
    cfg = session.get(LdapConfig, 1)
    if cfg is None:
        cfg = LdapConfig(id=1)
    for key, value in data.items():
        setattr(cfg, key, value)
    cfg.updated_at = datetime.now(timezone.utc)
    session.add(cfg)
    session.commit()
    session.refresh(cfg)
    return cfg


def get_user_group_server_group_ids(session: Session, user_group_id: int) -> list[int]:
    rows = session.exec(
        select(UserGroupServerGroup).where(UserGroupServerGroup.user_group_id == user_group_id)
    ).all()
    return [r.server_group_id for r in rows]


def set_user_group_server_groups(session: Session, user_group_id: int, server_group_ids: list[int]) -> bool:
    g = session.get(UserGroup, user_group_id)
    if not g:
        return False
    old = session.exec(
        select(UserGroupServerGroup).where(UserGroupServerGroup.user_group_id == user_group_id)
    ).all()
    for r in old:
        session.delete(r)
    for sgid in server_group_ids:
        session.add(UserGroupServerGroup(user_group_id=user_group_id, server_group_id=sgid))
    session.commit()
    return True


def get_allowed_agent_ids(session: Session, username: str, role: str) -> Optional[set]:
    """
    Returns None  → user sees ALL agents (admin or no restrictions).
    Returns set   → user sees only these agent_ids.
    Logic:
      - admin → None (all)
      - user in no groups → None (all, backward compat)
      - user in groups that have NO server groups assigned → None (all)
      - otherwise → union of agent_ids from all assigned server groups
    """
    if role == "admin":
        return None

    memberships = session.exec(
        select(UserGroupMember).where(UserGroupMember.username == username)
    ).all()

    if not memberships:
        return None  # brak grup → widzi wszystko

    allowed: set = set()
    for m in memberships:
        sg_rows = session.exec(
            select(UserGroupServerGroup)
            .where(UserGroupServerGroup.user_group_id == m.group_id)
        ).all()
        if not sg_rows:
            return None  # ta grupa nie ma ograniczeń → widzi wszystko
        for row in sg_rows:
            members = session.exec(
                select(ServerGroupMember)
                .where(ServerGroupMember.group_id == row.server_group_id)
            ).all()
            for sm in members:
                allowed.add(sm.agent_id)

    return allowed


# ── Revoked tokens ────────────────────────────────────────────────────────────

def revoke_token(session: Session, jti: str, expires_at: datetime) -> None:
    session.merge(RevokedToken(jti=jti, expires_at=expires_at))
    session.commit()


def is_token_revoked(jti: str) -> bool:
    """Synchronous check (no session arg) — creates own short-lived connection."""
    with Session(engine) as s:
        return s.get(RevokedToken, jti) is not None


def cleanup_revoked_tokens(session: Session) -> None:
    """Remove RevokedToken entries whose tokens have already expired."""
    now = datetime.now(timezone.utc)
    old = session.exec(
        select(RevokedToken).where(RevokedToken.expires_at < now)
    ).all()
    for r in old:
        session.delete(r)
    if old:
        session.commit()


# ── Audit log ─────────────────────────────────────────────────────────────────

def log_audit(
    session: Session,
    action: str,
    username: str = "",
    ip: str = "",
    detail: str = "",
) -> None:
    entry = AuditLog(action=action, username=username, ip=ip, detail=detail)
    session.add(entry)
    session.commit()


def get_audit_logs(session: Session, limit: int = 200) -> list[AuditLog]:
    return list(
        session.exec(
            select(AuditLog).order_by(AuditLog.occurred_at.desc()).limit(limit)
        ).all()
    )


# ── Active sessions ────────────────────────────────────────────────────────────

def create_session(
    session: Session,
    jti: str,
    username: str,
    ip: str,
    user_agent: str,
    expires_at: datetime,
) -> None:
    session.merge(ActiveSession(
        jti=jti,
        username=username,
        ip=ip,
        user_agent=user_agent[:256],
        expires_at=expires_at,
    ))
    session.commit()


def get_active_sessions(session: Session, username: Optional[str] = None) -> list[ActiveSession]:
    """Return non-expired, non-revoked sessions. Pass username=None for all (admin)."""
    now = datetime.now(timezone.utc)
    stmt = select(ActiveSession).where(ActiveSession.expires_at > now)
    if username:
        stmt = stmt.where(ActiveSession.username == username)
    sessions = list(session.exec(stmt.order_by(ActiveSession.created_at.desc())).all())
    # Filter out revoked tokens
    revoked = {r.jti for r in session.exec(select(RevokedToken)).all()}
    return [s for s in sessions if s.jti not in revoked]


def delete_session(session: Session, jti: str, expires_at: Optional[datetime] = None) -> bool:
    """Revoke a session — removes from ActiveSession and adds to RevokedToken."""
    s = session.get(ActiveSession, jti)
    if not s:
        return False
    exp = expires_at or s.expires_at
    session.merge(RevokedToken(jti=jti, expires_at=exp))
    session.delete(s)
    session.commit()
    return True


def cleanup_expired_sessions(session: Session) -> None:
    now = datetime.now(timezone.utc)
    old = list(session.exec(select(ActiveSession).where(ActiveSession.expires_at < now)).all())
    for s in old:
        session.delete(s)
    if old:
        session.commit()


# ── Agent token rotation ───────────────────────────────────────────────────────

def get_agent_token(session: Session) -> Optional[str]:
    """Returns decrypted DB agent token, or None if not set (use env var)."""
    row = session.get(AgentToken, 1)
    if not row or not row.token_enc:
        return None
    try:
        return decrypt_secret(row.token_enc)
    except Exception:
        return None


def get_agent_token_info(session: Session) -> Optional[dict]:
    row = session.get(AgentToken, 1)
    if not row or not row.token_enc:
        return None
    return {
        "source":     "db",
        "created_at": row.created_at.isoformat(),
        "created_by": row.created_by,
    }


def set_agent_token(session: Session, plain_token: str, created_by: str) -> None:
    session.merge(AgentToken(
        id=1,
        token_enc=encrypt_secret(plain_token),
        created_by=created_by,
    ))
    session.commit()


def set_user_group_members(session: Session, group_id: int, usernames: list[str]) -> bool:
    g = session.get(UserGroup, group_id)
    if not g:
        return False
    old = session.exec(
        select(UserGroupMember).where(UserGroupMember.group_id == group_id)
    ).all()
    for m in old:
        session.delete(m)
    for uname in usernames:
        session.add(UserGroupMember(group_id=group_id, username=uname))
    session.commit()
    return True
