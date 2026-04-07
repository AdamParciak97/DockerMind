"""
routers/settings.py — User management, server groups, user groups.

PUT  /api/settings/password           change own password
GET  /api/users                       list all users (admin)
POST /api/users                       create user (admin)
DELETE /api/users/{username}          delete user (admin)

GET    /api/server-groups             list server groups
POST   /api/server-groups             create
DELETE /api/server-groups/{id}        delete
PUT    /api/server-groups/{id}/members  update member list

GET    /api/user-groups               list user groups
POST   /api/user-groups               create
DELETE /api/user-groups/{id}          delete
PUT    /api/user-groups/{id}/members  update member list
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from auth import get_current_user, hash_password
from config import settings
from models import (
    create_db_user,
    create_server_group,
    create_user_group,
    delete_db_user,
    delete_server_group,
    delete_user_group,
    get_all_users,
    get_db_user,
    get_server_groups,
    get_session,
    get_user_groups,
    set_server_group_members,
    set_user_group_members,
    update_db_user_password,
)

router = APIRouter(tags=["settings"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_admin(user: str = Depends(get_current_user)) -> str:
    if user != settings.CT_USERNAME:
        from models import get_session as _gs
        # DB admin check handled in endpoint — just pass username through
        pass
    return user


def _is_admin(user: str, session: Session) -> bool:
    if user == settings.CT_USERNAME:
        return True
    db_user = get_db_user(session, user)
    return db_user is not None and db_user.role == "admin"


# ── Password ──────────────────────────────────────────────────────────────────

class PasswordChange(BaseModel):
    current_password: str
    new_password: str


@router.put("/api/settings/password")
async def change_password(
    body: PasswordChange,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
    from auth import verify_password, verify_db_password

    if user == settings.CT_USERNAME:
        raise HTTPException(
            status_code=400,
            detail="Hasło administratora środowiskowego zmień w pliku .env (CT_PASSWORD).",
        )
    db_user = get_db_user(session, user)
    if not db_user:
        raise HTTPException(status_code=404, detail="Użytkownik nie istnieje.")
    if not verify_db_password(body.current_password, db_user.hashed_password):
        raise HTTPException(status_code=400, detail="Nieprawidłowe aktualne hasło.")
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="Nowe hasło musi mieć co najmniej 6 znaków.")
    update_db_user_password(session, user, hash_password(body.new_password))
    return {"ok": True}


# ── User management ───────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "user"


@router.get("/api/users")
async def list_users(
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
    if not _is_admin(user, session):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    env_admin = {"username": settings.CT_USERNAME, "role": "admin", "id": 0,
                 "created_at": None, "source": "env"}
    db_users = [
        {"username": u.username, "role": u.role, "id": u.id,
         "created_at": u.created_at.isoformat(), "source": "db"}
        for u in get_all_users(session)
    ]
    return [env_admin] + db_users


@router.post("/api/users", status_code=201)
async def create_user(
    body: UserCreate,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
    if not _is_admin(user, session):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    if not body.username.strip():
        raise HTTPException(status_code=400, detail="Nazwa użytkownika jest wymagana.")
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Hasło musi mieć co najmniej 6 znaków.")
    if body.username == settings.CT_USERNAME:
        raise HTTPException(status_code=409, detail="Taki użytkownik już istnieje.")
    if get_db_user(session, body.username):
        raise HTTPException(status_code=409, detail="Taki użytkownik już istnieje.")
    role = body.role if body.role in ("admin", "user") else "user"
    u = create_db_user(session, body.username, hash_password(body.password), role)
    return {"username": u.username, "role": u.role, "id": u.id,
            "created_at": u.created_at.isoformat(), "source": "db"}


@router.delete("/api/users/{username}")
async def delete_user(
    username: str,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
    if not _is_admin(user, session):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    if username == settings.CT_USERNAME:
        raise HTTPException(status_code=400, detail="Nie można usunąć administratora środowiskowego.")
    if username == user:
        raise HTTPException(status_code=400, detail="Nie możesz usunąć własnego konta.")
    if not delete_db_user(session, username):
        raise HTTPException(status_code=404, detail="Użytkownik nie znaleziony.")
    return {"deleted": username}


# ── Server groups ─────────────────────────────────────────────────────────────

class ServerGroupCreate(BaseModel):
    name: str
    color: str = "#3b82f6"


class MembersUpdate(BaseModel):
    members: list[str]


@router.get("/api/server-groups")
async def list_server_groups(
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
    return get_server_groups(session)


@router.post("/api/server-groups", status_code=201)
async def create_srv_group(
    body: ServerGroupCreate,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
    if not _is_admin(user, session):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Nazwa grupy jest wymagana.")
    try:
        return create_server_group(session, body.name.strip(), body.color)
    except Exception:
        raise HTTPException(status_code=409, detail=f"Grupa '{body.name}' już istnieje.")


@router.delete("/api/server-groups/{group_id}")
async def delete_srv_group(
    group_id: int,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
    if not _is_admin(user, session):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    if not delete_server_group(session, group_id):
        raise HTTPException(status_code=404, detail="Grupa nie znaleziona.")
    return {"deleted": group_id}


@router.put("/api/server-groups/{group_id}/members")
async def update_srv_group_members(
    group_id: int,
    body: MembersUpdate,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
    if not _is_admin(user, session):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    if not set_server_group_members(session, group_id, body.members):
        raise HTTPException(status_code=404, detail="Grupa nie znaleziona.")
    groups = get_server_groups(session)
    return next((g for g in groups if g["id"] == group_id), {})


# ── User groups ───────────────────────────────────────────────────────────────

class UserGroupCreate(BaseModel):
    name: str


@router.get("/api/user-groups")
async def list_user_groups(
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
    return get_user_groups(session)


@router.post("/api/user-groups", status_code=201)
async def create_usr_group(
    body: UserGroupCreate,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
    if not _is_admin(user, session):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Nazwa grupy jest wymagana.")
    try:
        return create_user_group(session, body.name.strip())
    except Exception:
        raise HTTPException(status_code=409, detail=f"Grupa '{body.name}' już istnieje.")


@router.delete("/api/user-groups/{group_id}")
async def delete_usr_group(
    group_id: int,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
    if not _is_admin(user, session):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    if not delete_user_group(session, group_id):
        raise HTTPException(status_code=404, detail="Grupa nie znaleziona.")
    return {"deleted": group_id}


@router.put("/api/user-groups/{group_id}/members")
async def update_usr_group_members(
    group_id: int,
    body: MembersUpdate,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
    if not _is_admin(user, session):
        raise HTTPException(status_code=403, detail="Brak uprawnień.")
    if not set_user_group_members(session, group_id, body.members):
        raise HTTPException(status_code=404, detail="Grupa nie znaleziona.")
    groups = get_user_groups(session)
    return next((g for g in groups if g["id"] == group_id), {})
