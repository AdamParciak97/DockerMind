"""
routers/settings.py — User management, server groups, user groups, LDAP config.

PUT  /api/settings/password           change own password
GET  /api/settings/ldap               get LDAP config (admin)
PUT  /api/settings/ldap               save LDAP config (admin)
POST /api/settings/ldap/test          test LDAP connection (admin)
GET  /api/users                       list all users (admin)
POST /api/users                       create user (admin)
DELETE /api/users/{username}          delete user (admin)

GET  /api/audit-logs                  audit log (admin)

GET    /api/server-groups             list server groups
POST   /api/server-groups             create
DELETE /api/server-groups/{id}        delete
PUT    /api/server-groups/{id}/members  update member list

GET    /api/user-groups               list user groups
POST   /api/user-groups               create
DELETE /api/user-groups/{id}          delete
PUT    /api/user-groups/{id}/members  update member list
"""

import io
import secrets
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session

from auth import get_current_user_info, hash_password, validate_password_strength
from config import settings
from models import (
    create_db_user,
    create_server_group,
    create_user_group,
    decrypt_secret,
    delete_db_user,
    delete_server_group,
    delete_user_group,
    delete_session,
    encrypt_secret,
    get_active_sessions,
    get_agent_token,
    get_agent_token_info,
    get_all_users,
    get_audit_logs,
    get_db_user,
    get_ldap_config,
    get_server_groups,
    get_session,
    get_user_groups,
    log_audit,
    save_ldap_config,
    set_agent_token,
    set_server_group_members,
    set_user_group_members,
    set_user_group_server_groups,
    update_db_user_password,
)

router = APIRouter(tags=["settings"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_admin(info: dict) -> bool:
    """
    Check admin role using JWT payload (covers env admin, DB admins, LDAP admins).
    The role claim is set at login time and signed into the JWT.
    """
    return info.get("role") == "admin"


# ── Password ──────────────────────────────────────────────────────────────────

class PasswordChange(BaseModel):
    current_password: str
    new_password: str


@router.put("/api/settings/password")
async def change_password(
    body: PasswordChange,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    from auth import verify_password, verify_db_password

    user = info["username"]
    if user == settings.CT_USERNAME:
        raise HTTPException(
            status_code=400,
            detail="Hasło administratora środowiskowego zmień w pliku .env (CT_PASSWORD).",
        )
    db_user = get_db_user(session, user)
    if not db_user:
        raise HTTPException(status_code=404, detail="Użytkownik nie istnieje.")
    if db_user.source == "ldap":
        raise HTTPException(status_code=400, detail="Użytkownicy LDAP zmieniają hasło w Active Directory.")
    if not verify_db_password(body.current_password, db_user.hashed_password):
        raise HTTPException(status_code=400, detail="Nieprawidłowe aktualne hasło.")
    err = validate_password_strength(body.new_password)
    if err:
        raise HTTPException(status_code=400, detail=err)
    update_db_user_password(session, user, hash_password(body.new_password))
    log_audit(session, "password_changed", username=user)
    return {"ok": True}


# ── User management ───────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    username: str
    password: str = ""
    role: str = "user"
    source: str = "db"   # "db" | "ldap"


@router.get("/api/users")
async def list_users(
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    if not _is_admin(info):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    env_admin = {"username": "admin (env)", "role": "admin", "id": 0,
                 "created_at": None, "source": "env"}
    db_users = [
        {"username": u.username, "role": u.role, "id": u.id,
         "created_at": u.created_at.isoformat(), "source": u.source}
        for u in get_all_users(session)
    ]
    return [env_admin] + db_users


@router.post("/api/users", status_code=201)
async def create_user(
    body: UserCreate,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    if not _is_admin(info):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    if not body.username.strip():
        raise HTTPException(status_code=400, detail="Nazwa użytkownika jest wymagana.")
    if not _USERNAME_RE.match(body.username):
        raise HTTPException(
            status_code=400,
            detail="Nazwa użytkownika może zawierać tylko litery, cyfry oraz znaki: . _ @ -",
        )
    source = body.source if body.source in ("db", "ldap") else "db"
    if source == "db":
        err = validate_password_strength(body.password)
        if err:
            raise HTTPException(status_code=400, detail=err)
        hashed = hash_password(body.password)
    else:
        hashed = ""  # LDAP stub — password not stored
    if body.username == settings.CT_USERNAME:
        raise HTTPException(status_code=409, detail="Taki użytkownik już istnieje.")
    if get_db_user(session, body.username):
        raise HTTPException(status_code=409, detail="Taki użytkownik już istnieje.")
    role = body.role if body.role in ("admin", "user") else "user"
    u = create_db_user(session, body.username, hashed, role, source=source)
    log_audit(session, "user_created", username=info["username"],
              detail=f"new_user={u.username} role={u.role}")
    return {"username": u.username, "role": u.role, "id": u.id,
            "created_at": u.created_at.isoformat(), "source": u.source}


@router.get("/api/audit-logs")
async def list_audit_logs(
    limit: int = Query(default=200, ge=1, le=2000),
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    if not _is_admin(info):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    logs = get_audit_logs(session, limit=limit)
    return [
        {
            "id": lg.id,
            "action": lg.action,
            "username": lg.username,
            "ip": lg.ip,
            "detail": lg.detail,
            "occurred_at": lg.occurred_at.isoformat(),
        }
        for lg in logs
    ]


@router.delete("/api/users/{username}")
async def delete_user(
    username: str,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    if not _is_admin(info):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    if username == settings.CT_USERNAME:
        raise HTTPException(status_code=400, detail="Nie można usunąć administratora środowiskowego.")
    if username == info["username"]:
        raise HTTPException(status_code=400, detail="Nie możesz usunąć własnego konta.")
    if not delete_db_user(session, username):
        raise HTTPException(status_code=404, detail="Użytkownik nie znaleziony.")
    log_audit(session, "user_deleted", username=info["username"], detail=f"deleted={username}")
    return {"deleted": username}


# ── Server groups ─────────────────────────────────────────────────────────────

import re as _re

_COLOR_RE    = _re.compile(r'^#[0-9a-fA-F]{6}$')
_USERNAME_RE = _re.compile(r'^[a-zA-Z0-9._@\-]{1,64}$')


class ServerGroupCreate(BaseModel):
    name: str
    color: str = "#3b82f6"

    def validate_color(self) -> None:
        if not _COLOR_RE.match(self.color):
            raise ValueError("Nieprawidłowy format koloru (wymagany #rrggbb).")


class MembersUpdate(BaseModel):
    members: list[str]


@router.get("/api/server-groups")
async def list_server_groups(
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    if not _is_admin(info):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    return get_server_groups(session)


@router.post("/api/server-groups", status_code=201)
async def create_srv_group(
    body: ServerGroupCreate,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    if not _is_admin(info):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Nazwa grupy jest wymagana.")
    if not _COLOR_RE.match(body.color):
        raise HTTPException(status_code=400, detail="Nieprawidłowy format koloru (wymagany #rrggbb).")
    try:
        grp = create_server_group(session, body.name.strip(), body.color)
    except Exception:
        raise HTTPException(status_code=409, detail="Grupa o podanej nazwie już istnieje.")
    log_audit(session, "server_group_created", username=info["username"], detail=f"name={grp['name']}")
    return grp


@router.delete("/api/server-groups/{group_id}")
async def delete_srv_group(
    group_id: int,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    if not _is_admin(info):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    if not delete_server_group(session, group_id):
        raise HTTPException(status_code=404, detail="Grupa nie znaleziona.")
    log_audit(session, "server_group_deleted", username=info["username"], detail=f"id={group_id}")
    return {"deleted": group_id}


@router.put("/api/server-groups/{group_id}/members")
async def update_srv_group_members(
    group_id: int,
    body: MembersUpdate,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    if not _is_admin(info):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    if not set_server_group_members(session, group_id, body.members):
        raise HTTPException(status_code=404, detail="Grupa nie znaleziona.")
    log_audit(session, "server_group_members_updated", username=info["username"],
              detail=f"id={group_id}")
    groups = get_server_groups(session)
    return next((g for g in groups if g["id"] == group_id), {})


# ── User groups ───────────────────────────────────────────────────────────────

class UserGroupCreate(BaseModel):
    name: str


@router.get("/api/user-groups")
async def list_user_groups(
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    if not _is_admin(info):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    return get_user_groups(session)


@router.post("/api/user-groups", status_code=201)
async def create_usr_group(
    body: UserGroupCreate,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    if not _is_admin(info):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Nazwa grupy jest wymagana.")
    try:
        grp = create_user_group(session, body.name.strip())
    except Exception:
        raise HTTPException(status_code=409, detail="Grupa o podanej nazwie już istnieje.")
    log_audit(session, "user_group_created", username=info["username"], detail=f"name={grp['name']}")
    return grp


@router.delete("/api/user-groups/{group_id}")
async def delete_usr_group(
    group_id: int,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    if not _is_admin(info):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    if not delete_user_group(session, group_id):
        raise HTTPException(status_code=404, detail="Grupa nie znaleziona.")
    log_audit(session, "user_group_deleted", username=info["username"], detail=f"id={group_id}")
    return {"deleted": group_id}


@router.put("/api/user-groups/{group_id}/members")
async def update_usr_group_members(
    group_id: int,
    body: MembersUpdate,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    if not _is_admin(info):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    if not set_user_group_members(session, group_id, body.members):
        raise HTTPException(status_code=404, detail="Grupa nie znaleziona.")
    log_audit(session, "user_group_members_updated", username=info["username"],
              detail=f"id={group_id}")
    groups = get_user_groups(session)
    return next((g for g in groups if g["id"] == group_id), {})


class ServerGroupsUpdate(BaseModel):
    server_group_ids: list[int]


@router.put("/api/user-groups/{group_id}/server-groups")
async def update_usr_group_server_groups(
    group_id: int,
    body: ServerGroupsUpdate,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    if not _is_admin(info):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    if not set_user_group_server_groups(session, group_id, body.server_group_ids):
        raise HTTPException(status_code=404, detail="Grupa nie znaleziona.")
    groups = get_user_groups(session)
    return next((g for g in groups if g["id"] == group_id), {})


# ── Active sessions ───────────────────────────────────────────────────────────

@router.get("/api/settings/sessions")
async def list_sessions(
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    """Admin sees all sessions; regular users see only their own."""
    username = None if _is_admin(info) else info["username"]
    sessions = get_active_sessions(session, username=username)
    current_jti = info.get("jti", "")
    return [
        {
            "jti":        s.jti,
            "username":   s.username,
            "ip":         s.ip,
            "user_agent": s.user_agent,
            "created_at": s.created_at.isoformat(),
            "expires_at": s.expires_at.isoformat(),
            "is_current": s.jti == current_jti,
        }
        for s in sessions
    ]


@router.delete("/api/settings/sessions/{jti}", status_code=204)
async def revoke_session_endpoint(
    jti: str,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    sessions = get_active_sessions(session)
    target = next((s for s in sessions if s.jti == jti), None)
    if not target:
        raise HTTPException(status_code=404, detail="Sesja nie znaleziona.")
    # Non-admin can only revoke their own sessions
    if not _is_admin(info) and target.username != info["username"]:
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    if not delete_session(session, jti):
        raise HTTPException(status_code=404, detail="Sesja nie znaleziona.")
    log_audit(session, "session_revoked", username=info["username"],
              detail=f"revoked jti={jti[:8]}... of user={target.username}")


# ── Agent token rotation ───────────────────────────────────────────────────────

@router.get("/api/settings/agent-token")
async def get_agent_token_endpoint(
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    if not _is_admin(info):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    db_info = get_agent_token_info(session)
    if db_info:
        return db_info
    # Env token in use — return masked info
    token = settings.AGENT_SECRET_TOKEN
    return {
        "source":     "env",
        "created_at": None,
        "created_by": None,
        "masked":     (token[:4] + "…" + token[-4:]) if len(token) > 8 else "****",
    }


@router.post("/api/settings/agent-token/rotate")
async def rotate_agent_token(
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    if not _is_admin(info):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    new_token = secrets.token_hex(32)
    set_agent_token(session, new_token, created_by=info["username"])
    log_audit(session, "agent_token_rotated", username=info["username"])
    return {
        "token": new_token,
        "warning": "Zapisz ten token — nie będzie pokazany ponownie. "
                   "Zaktualizuj go we wszystkich agentach i zrestartuj je.",
    }


# ── Database backup ────────────────────────────────────────────────────────────

@router.get("/api/settings/backup")
async def download_backup(
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    if not _is_admin(info):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    log_audit(session, "backup_downloaded", username=info["username"])
    try:
        buf = io.BytesIO()
        src = sqlite3.connect(settings.DB_PATH)
        dst = sqlite3.connect(":memory:")
        src.backup(dst)
        src.close()
        # Serialize in-memory DB to bytes
        for chunk in dst.iterdump():
            pass  # Trigger WAL checkpoint
        dst.close()
        # Re-open from file with backup() to bytes
        buf = io.BytesIO()
        src2 = sqlite3.connect(settings.DB_PATH)
        # Use a temporary in-memory DB then serialize
        mem = sqlite3.connect(":memory:")
        src2.backup(mem)
        src2.close()
        # Write mem DB to bytes via file-like object trick
        tmp_path = "/tmp/dm_backup.sqlite"
        out = sqlite3.connect(tmp_path)
        mem.backup(out)
        mem.close()
        out.close()
        with open(tmp_path, "rb") as f:
            data = f.read()
        import os
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    except Exception as e:
        import logging as _log
        _log.getLogger(__name__).error("Backup failed: %s", e)
        raise HTTPException(status_code=500, detail="Błąd tworzenia backupu.")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"dockermind-backup-{timestamp}.sqlite"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── LDAP configuration ────────────────────────────────────────────────────────

_PASSWORD_MASK = "********"


class LdapConfigUpdate(BaseModel):
    enabled: bool = False
    server: str = ""
    port: int = 389
    use_ssl: bool = False
    use_tls: bool = False
    tls_verify: bool = True
    bind_dn: str = ""
    bind_password: str = ""   # plain text; empty = keep existing; _PASSWORD_MASK = keep existing
    base_dn: str = ""
    user_filter: str = "(sAMAccountName={username})"
    admin_group_dn: str = ""


class LdapTestRequest(BaseModel):
    test_username: str = ""   # optional: search for this user after service-account bind


def _ldap_config_to_dict(cfg) -> dict:
    return {
        "enabled":        cfg.enabled,
        "server":         cfg.server,
        "port":           cfg.port,
        "use_ssl":        cfg.use_ssl,
        "use_tls":        cfg.use_tls,
        "tls_verify":     cfg.tls_verify,
        "bind_dn":        cfg.bind_dn,
        "bind_password":  _PASSWORD_MASK if cfg.bind_password_enc else "",
        "base_dn":        cfg.base_dn,
        "user_filter":    cfg.user_filter,
        "admin_group_dn": cfg.admin_group_dn,
        "updated_at":     cfg.updated_at.isoformat(),
    }


@router.get("/api/settings/ldap")
async def get_ldap_settings(
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    if not _is_admin(info):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    cfg = get_ldap_config(session)
    if cfg is None:
        return {
            "enabled": False, "server": "", "port": 389,
            "use_ssl": False, "use_tls": False, "tls_verify": True,
            "bind_dn": "", "bind_password": "", "base_dn": "",
            "user_filter": "(sAMAccountName={username})",
            "admin_group_dn": "", "updated_at": None,
        }
    return _ldap_config_to_dict(cfg)


@router.put("/api/settings/ldap")
async def update_ldap_settings(
    body: LdapConfigUpdate,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    if not _is_admin(info):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")

    existing = get_ldap_config(session)

    # Resolve password: keep existing if mask or empty sent
    if body.bind_password and body.bind_password != _PASSWORD_MASK:
        new_enc = encrypt_secret(body.bind_password)
    elif existing:
        new_enc = existing.bind_password_enc
    else:
        new_enc = ""

    cfg = save_ldap_config(session, {
        "enabled":          body.enabled,
        "server":           body.server.strip(),
        "port":             body.port,
        "use_ssl":          body.use_ssl,
        "use_tls":          body.use_tls,
        "tls_verify":       body.tls_verify,
        "bind_dn":          body.bind_dn.strip(),
        "bind_password_enc": new_enc,
        "base_dn":          body.base_dn.strip(),
        "user_filter":      body.user_filter.strip() or "(sAMAccountName={username})",
        "admin_group_dn":   body.admin_group_dn.strip(),
    })
    return _ldap_config_to_dict(cfg)


@router.post("/api/settings/ldap/test")
async def test_ldap_connection(
    body: LdapTestRequest,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    if not _is_admin(info):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")

    from ldap_auth import test_ldap_service_bind
    cfg = get_ldap_config(session)
    if cfg is None or not cfg.server or not cfg.base_dn:
        raise HTTPException(status_code=400, detail="LDAP nie jest skonfigurowany. Zapisz ustawienia najpierw.")

    bind_password = decrypt_secret(cfg.bind_password_enc) if cfg.bind_password_enc else ""

    result = test_ldap_service_bind(
        server=cfg.server,
        port=cfg.port,
        use_ssl=cfg.use_ssl,
        use_tls=cfg.use_tls,
        tls_verify=cfg.tls_verify,
        bind_dn=cfg.bind_dn,
        bind_password=bind_password,
        base_dn=cfg.base_dn,
        user_filter=cfg.user_filter,
        test_username=body.test_username.strip() if body.test_username else "",
    )
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result
