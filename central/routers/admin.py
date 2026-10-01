"""Administrator operations: image inventory, container inspection and task center."""
import json
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from auth import get_current_user_info
from models import CommandHistory, get_allowed_agent_ids, get_session, log_audit
from websocket_manager import manager

router = APIRouter(tags=["admin"])


def _admin(info: dict) -> None:
    if info.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Wymagana rola administratora.")


def _agent(session: Session, info: dict, agent_id: str) -> dict:
    allowed = get_allowed_agent_ids(session, info["username"], info["role"])
    if allowed is not None and agent_id not in allowed:
        raise HTTPException(status_code=403, detail="Brak dostępu do tego serwera.")
    agent = manager.get_agent(agent_id)
    if not agent or not agent["online"]:
        raise HTTPException(status_code=503, detail="Agent jest offline.")
    return agent


class ImageAction(BaseModel):
    action: str = Field(pattern=r"^(pull|remove|prune)$")
    reference: str = Field(default="", max_length=300)


@router.get("/api/servers/{agent_id}/images")
async def images(agent_id: str, session: Session = Depends(get_session), info: dict = Depends(get_current_user_info)):
    _admin(info)
    _agent(session, info, agent_id)
    try:
        return await manager.request_from_agent(agent_id, "list_images")
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/api/servers/{agent_id}/images/action")
async def image_action(agent_id: str, body: ImageAction, session: Session = Depends(get_session), info: dict = Depends(get_current_user_info)):
    _admin(info)
    _agent(session, info, agent_id)
    if body.action in {"remove", "pull"} and not body.reference.strip():
        raise HTTPException(status_code=400, detail="Podaj nazwę obrazu.")
    try:
        result = await manager.request_from_agent(agent_id, "image_action", body.model_dump())
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    log_audit(session, "image_action", username=info["username"], detail=json.dumps({"agent_id": agent_id, **body.model_dump()}))
    return result


@router.get("/api/servers/{agent_id}/containers/{container_name}/inspect")
async def inspect_container(agent_id: str, container_name: str, session: Session = Depends(get_session), info: dict = Depends(get_current_user_info)):
    _admin(info)
    _agent(session, info, agent_id)
    try:
        return await manager.request_from_agent(agent_id, "inspect_container", {"container": container_name})
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/api/tasks")
async def task_center(limit: int = 100, session: Session = Depends(get_session), info: dict = Depends(get_current_user_info)):
    _admin(info)
    limit = max(1, min(limit, 200))
    rows = session.exec(select(CommandHistory).order_by(CommandHistory.created_at.desc()).limit(limit)).all()
    return [{
        "id": row.id, "type": "command", "username": row.username,
        "status": row.status, "timeout": row.timeout,
        "targets": json.loads(row.targets_json), "created_at": row.created_at.isoformat(),
        "command_hash": row.command_hash,
    } for row in rows]


@router.get("/api/admin/capabilities")
async def admin_capabilities(info: dict = Depends(get_current_user_info)):
    _admin(info)
    return {
        "features": ["image_inventory", "image_actions", "container_inspect", "task_center", "role_management"],
        "server_time": time.time(),
    }
