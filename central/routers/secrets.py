"""
routers/secrets.py — Encrypted secret management.

GET    /api/secrets              list (names + metadata, no values)
POST   /api/secrets              create
PUT    /api/secrets/{id}         update
GET    /api/secrets/{id}/reveal  return decrypted value
DELETE /api/secrets/{id}         delete
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from auth import get_current_user, get_current_user_info
from models import (
    Secret,
    decrypt_secret,
    delete_secret,
    encrypt_secret,
    get_secret,
    get_secrets,
    get_session,
)

router = APIRouter(tags=["secrets"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class SecretCreate(BaseModel):
    name: str
    value: str
    description: str = ""


class SecretUpdate(BaseModel):
    value: str = ""
    description: str = ""


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/api/secrets")
async def list_secrets(
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
    return [_secret_meta(s) for s in get_secrets(session)]


@router.post("/api/secrets")
async def create_secret(
    body: SecretCreate,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Nazwa sekretu nie może być pusta.")
    sec = Secret(
        name=body.name.strip(),
        encrypted_value=encrypt_secret(body.value),
        description=body.description,
    )
    session.add(sec)
    try:
        session.commit()
    except Exception:
        raise HTTPException(status_code=409, detail=f"Sekret '{body.name}' już istnieje.")
    session.refresh(sec)
    return _secret_meta(sec)


@router.put("/api/secrets/{secret_id}")
async def update_secret(
    secret_id: int,
    body: SecretUpdate,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
    sec = get_secret(session, secret_id)
    if not sec:
        raise HTTPException(status_code=404, detail="Sekret nie znaleziony.")
    if body.value:
        sec.encrypted_value = encrypt_secret(body.value)
    sec.description = body.description
    sec.updated_at = datetime.now(timezone.utc)
    session.add(sec)
    session.commit()
    return _secret_meta(sec)


@router.get("/api/secrets/{secret_id}/reveal")
async def reveal_secret(
    secret_id: int,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    if info.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Tylko administrator może odczytać wartość sekretu.")
    sec = get_secret(session, secret_id)
    if not sec:
        raise HTTPException(status_code=404, detail="Sekret nie znaleziony.")
    try:
        value = decrypt_secret(sec.encrypted_value)
    except Exception:
        raise HTTPException(status_code=500, detail="Błąd deszyfrowania sekretu.")
    return {"id": sec.id, "name": sec.name, "value": value}


@router.delete("/api/secrets/{secret_id}")
async def remove_secret(
    secret_id: int,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
    if not delete_secret(session, secret_id):
        raise HTTPException(status_code=404, detail="Sekret nie znaleziony.")
    return {"deleted": secret_id}


# ── Serializer ────────────────────────────────────────────────────────────────

def _secret_meta(s: Secret) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }
