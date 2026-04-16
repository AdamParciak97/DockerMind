"""
routers/servers.py — Server and container REST endpoints.

GET  /api/servers
GET  /api/servers/{agent_id}
GET  /api/servers/{agent_id}/containers
GET  /api/servers/{agent_id}/containers/{name}/logs?lines=200
GET  /api/servers/{agent_id}/containers/{name}/compose
GET  /api/health
"""

import json
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlmodel import Session

from auth import get_current_user_info
from models import get_allowed_agent_ids, get_session, log_audit
from websocket_manager import manager

router = APIRouter(tags=["servers"])


def _check_agent_access(agent_id: str, info: dict, session: Session) -> None:
    """Raises 403 if user has no access to this agent."""
    allowed = get_allowed_agent_ids(session, info["username"], info["role"])
    if allowed is not None and agent_id not in allowed:
        raise HTTPException(status_code=403, detail="Brak dostępu do tego serwera.")


# ── Health ────────────────────────────────────────────────────────────────────

@router.get("/api/health")
async def health(info: dict = Depends(get_current_user_info)):
    agents = manager.get_all_agents()
    online = [a for a in agents if a["online"]]
    return {
        "status": "ok",
        "timestamp": time.time(),
        "agents_total": len(agents),
        "agents_online": len(online),
    }


# ── Servers ───────────────────────────────────────────────────────────────────

@router.get("/api/servers")
async def list_servers(
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    allowed = get_allowed_agent_ids(session, info["username"], info["role"])
    agents = manager.get_agents_filtered(allowed)
    result = []
    for a in agents:
        containers = manager.get_agent_containers(a["agent_id"]) or []
        warning = any(c.get("restart_count", 0) > 3 for c in containers)
        running = sum(1 for c in containers if c.get("status") == "running")
        stopped = sum(1 for c in containers if c.get("status") in ("exited", "dead"))
        restarting = sum(1 for c in containers if c.get("status") == "restarting")
        result.append({
            "agent_id": a["agent_id"],
            "online": a["online"],
            "warning": warning,
            "last_seen": a["last_seen"],
            "info": a["info"],
            "container_count": len(containers),
            "containers_running": running,
            "containers_stopped": stopped,
            "containers_restarting": restarting,
        })
    return result


@router.get("/api/servers/{agent_id}")
async def get_server(
    agent_id: str,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    _check_agent_access(agent_id, info, session)
    data = manager.get_agent(agent_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Serwer '{agent_id}' nie znaleziony.")
    return data


# ── Containers ────────────────────────────────────────────────────────────────

@router.get("/api/servers/{agent_id}/containers")
async def list_containers(
    agent_id: str,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    _check_agent_access(agent_id, info, session)
    containers = manager.get_agent_containers(agent_id)
    if containers is None:
        raise HTTPException(status_code=404, detail=f"Serwer '{agent_id}' nie znaleziony.")
    return [
        {k: v for k, v in c.items() if k not in ("logs", "compose")}
        for c in containers
    ]


@router.get("/api/servers/{agent_id}/containers/{container_name}/logs")
async def get_logs(
    agent_id: str,
    container_name: str,
    lines: int = Query(default=200, ge=1, le=10000),
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    _check_agent_access(agent_id, info, session)
    _require_online(agent_id)
    try:
        result = await manager.request_from_agent(
            agent_id, action="get_logs",
            params={"container": container_name, "lines": lines},
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"container": container_name, "lines": lines, "logs": result}


@router.get("/api/servers/{agent_id}/containers/{container_name}/compose")
async def get_compose(
    agent_id: str,
    container_name: str,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    _check_agent_access(agent_id, info, session)
    _require_online(agent_id)
    try:
        result = await manager.request_from_agent(
            agent_id, action="get_compose",
            params={"container": container_name},
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"container": container_name, "compose": result}


class SaveComposeRequest(BaseModel):
    content: str


@router.put("/api/servers/{agent_id}/containers/{container_name}/compose")
async def save_compose(
    agent_id: str,
    container_name: str,
    body: SaveComposeRequest,
    request: Request,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    if info["role"] != "admin":
        raise HTTPException(status_code=403, detail="Edycja pliku compose wymaga roli administrator.")
    _check_agent_access(agent_id, info, session)
    _require_online(agent_id)
    try:
        result = await manager.request_from_agent(
            agent_id, action="save_compose",
            params={"container": container_name, "content": body.content},
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Błąd zapisu pliku."))
    log_audit(session, "compose_save", username=info["username"],
              detail=json.dumps({"agent_id": agent_id, "container": container_name}))
    return result


class ContainerActionRequest(BaseModel):
    action: str  # start | stop | restart


@router.post("/api/servers/{agent_id}/containers/{container_name}/action")
async def container_action(
    agent_id: str,
    container_name: str,
    body: ContainerActionRequest,
    request: Request,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    if info["role"] != "admin":
        raise HTTPException(status_code=403, detail="Akcje na kontenerach wymagają roli administrator.")
    _check_agent_access(agent_id, info, session)
    _require_online(agent_id)
    if body.action not in ("start", "stop", "restart"):
        raise HTTPException(status_code=400, detail="Akcja musi być: start, stop lub restart.")
    try:
        result = await manager.request_from_agent(
            agent_id, action="container_action",
            params={"container": container_name, "action": body.action},
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Błąd wykonania akcji."))
    log_audit(session, "container_action", username=info["username"],
              detail=json.dumps({"agent_id": agent_id, "container": container_name, "action": body.action}))
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_online(agent_id: str) -> None:
    if not manager.is_agent_online(agent_id):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Agent '{agent_id}' jest offline.",
        )
