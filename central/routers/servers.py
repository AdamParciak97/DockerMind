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
import hashlib
import logging
import re as _re
import time
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from auth import get_current_user_info
try:
    from models import CommandHistory, encrypt_secret, get_allowed_agent_ids, get_session, log_audit
except ImportError:  # lightweight route tests may provide a minimal models module
    from models import get_allowed_agent_ids, get_session, log_audit
    CommandHistory = None
    encrypt_secret = lambda value: value
from websocket_manager import manager

router = APIRouter(tags=["servers"])

_AGENT_ID_RE = _re.compile(r'^[a-z0-9][a-z0-9\-]*$')
_CONTAINER_NAME_RE = _re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.\-]*$')


def _validate_ids(agent_id: str, container_name: str = "") -> None:
    if not _AGENT_ID_RE.match(agent_id):
        raise HTTPException(status_code=400, detail="Nieprawidłowy identyfikator agenta.")
    if container_name and not _CONTAINER_NAME_RE.match(container_name):
        raise HTTPException(status_code=400, detail="Nieprawidłowa nazwa kontenera.")


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
            "display_name": a.get("display_name", ""),
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


class ServerProfileUpdate(BaseModel):
    display_name: str = ""


@router.put("/api/servers/{agent_id}/profile")
async def update_server_profile(
    agent_id: str,
    body: ServerProfileUpdate,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    from models import AgentProfile
    if info["role"] != "admin":
        raise HTTPException(status_code=403, detail="Zmiana nazwy wymaga roli administrator.")
    _validate_ids(agent_id)
    _check_agent_access(agent_id, info, session)
    if manager.get_agent(agent_id) is None:
        raise HTTPException(status_code=404, detail="Serwer nie znaleziony.")
    name = body.display_name.strip()
    if len(name) > 120 or any(ord(c) < 32 for c in name):
        raise HTTPException(status_code=400, detail="Nazwa może mieć maksymalnie 120 znaków bez znaków sterujących.")
    profile = session.get(AgentProfile, agent_id) or AgentProfile(agent_id=agent_id)
    profile.display_name = name
    session.add(profile)
    session.commit()
    manager.display_names[agent_id] = name
    data = {"agent_id": agent_id, "display_name": name}
    log_audit(session, "server_renamed", username=info["username"], detail=json.dumps(data))
    await manager.broadcast_to_dashboards("agent_updated", data)
    return data


@router.delete("/api/servers/{agent_id}")
async def delete_server(
    agent_id: str,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    if info["role"] != "admin":
        raise HTTPException(status_code=403, detail="Usuwanie serwera wymaga roli administrator.")
    _validate_ids(agent_id)
    _check_agent_access(agent_id, info, session)
    try:
        await manager.remove_offline_agent(agent_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Serwer nie znaleziony.")
    except ValueError:
        raise HTTPException(status_code=409, detail="Agent nadal ma połączenie. Poczekaj na rozłączenie i spróbuj ponownie.")
    log_audit(session, "server_delete", username=info["username"],
              detail=json.dumps({"agent_id": agent_id}))
    await manager.broadcast_to_dashboards("agent_removed", {"agent_id": agent_id})
    return {"agent_id": agent_id, "deleted": True}


@router.get("/api/servers/{agent_id}")
async def get_server(
    agent_id: str,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    _validate_ids(agent_id)
    _check_agent_access(agent_id, info, session)
    data = manager.get_agent(agent_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Serwer nie znaleziony.")
    return data


# ── Containers ────────────────────────────────────────────────────────────────

class HostCommandBody(BaseModel):
    command: str = Field(min_length=1, max_length=16000)
    timeout: int = Field(default=30, ge=1, le=120, strict=True)
    variables: dict[str, str] = Field(default_factory=dict, max_length=20)


_active_commands = 0


@router.post('/api/servers/{agent_id}/command')
async def host_command(agent_id: str, body: HostCommandBody,
                       session: Session = Depends(get_session),
                       info: dict = Depends(get_current_user_info)):
    global _active_commands
    if info['role'] != 'admin':
        raise HTTPException(403, 'Komendy hosta wymagają roli administrator.')
    _validate_ids(agent_id)
    _check_agent_access(agent_id, info, session)
    if not body.command.strip() or '\x00' in body.command:
        raise HTTPException(400, 'Podaj niepustą komendę bez znaku NUL.')
    agent = manager.get_agent(agent_id)
    if agent is None:
        raise HTTPException(404, 'Serwer nie istnieje.')
    _require_online(agent_id)
    capabilities = agent.get('info', {}).get('capabilities', {})
    if capabilities.get('command_protocol') != 1:
        raise HTTPException(409, 'Zaktualizuj agenta, aby wykonywać wspólne komendy.')
    if capabilities.get('host_commands') is not True:
        raise HTTPException(409, 'Dostęp do hosta wyłączony. Włącz HOST_ACCESS_ENABLED na agencie.')
    if _active_commands >= 16:
        raise HTTPException(429, 'Trwa już 16 komend. Poczekaj na zakończenie.')
    # Do not persist shell text or output: both may contain passwords or tokens.
    info_values = {'AGENT_ID': agent_id, 'SERVER_NAME': agent.get('display_name') or agent.get('info', {}).get('agent_name', agent_id),
                   'IP': agent.get('info', {}).get('ip', ''), 'HOSTNAME': agent.get('info', {}).get('hostname', '')}
    for key, value in body.variables.items():
        if key.isidentifier() and len(key) <= 40 and len(value) <= 500:
            info_values[key] = value
    rendered = body.command
    for key, value in info_values.items():
        rendered = rendered.replace('${' + key + '}', value)
    detail = {'agent_id': agent_id, 'command_sha256': hashlib.sha256(rendered.encode()).hexdigest(),
              'timeout': body.timeout}
    log_audit(session, 'host_command_started', username=info['username'], detail=json.dumps(detail))
    _active_commands += 1
    try:
        result = await manager.request_from_agent(agent_id, action='host_command',
                    params={'command': rendered, 'timeout': body.timeout}, timeout=body.timeout + 10)
        if not isinstance(result, dict) or type(result.get('exit_code')) is not int:
            raise RuntimeError('Agent zwrócił nieprawidłowy wynik komendy.')
        log_audit(session, 'host_command_finished', username=info['username'],
                  detail=json.dumps({**detail, 'exit_code': result['exit_code'],
                                     'timed_out': bool(result.get('timed_out'))}))
        if CommandHistory is not None:
            history = CommandHistory(username=info['username'], command_enc=encrypt_secret(body.command),
                                     command_hash=detail['command_sha256'], timeout=body.timeout,
                                     targets_json=json.dumps([agent_id]), status='completed')
            session.add(history); session.commit()
        return {**result, 'agent_id': agent_id, 'variables': info_values}
    except RuntimeError as exc:
        log_audit(session, 'host_command_failed', username=info['username'], detail=json.dumps(detail))
        raise HTTPException(503, str(exc)) from exc
    finally:
        _active_commands -= 1

@router.get("/api/servers/{agent_id}/containers")
async def list_containers(
    agent_id: str,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    _validate_ids(agent_id)
    _check_agent_access(agent_id, info, session)
    containers = manager.get_agent_containers(agent_id)
    if containers is None:
        raise HTTPException(status_code=404, detail="Serwer nie znaleziony.")
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
    _validate_ids(agent_id, container_name)
    _check_agent_access(agent_id, info, session)
    _require_online(agent_id)
    try:
        result = await manager.request_from_agent(
            agent_id, action="get_logs",
            params={"container": container_name, "lines": lines},
        )
    except RuntimeError as e:
        logger.warning("get_logs failed for %s/%s: %s", agent_id, container_name, e)
        raise HTTPException(status_code=503, detail="Agent nie odpowiedział. Spróbuj ponownie.")
    return {"container": container_name, "lines": lines, "logs": result}


@router.get("/api/servers/{agent_id}/containers/{container_name}/compose")
async def get_compose(
    agent_id: str,
    container_name: str,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    _validate_ids(agent_id, container_name)
    _check_agent_access(agent_id, info, session)
    _require_online(agent_id)
    try:
        result = await manager.request_from_agent(
            agent_id, action="get_compose",
            params={"container": container_name},
        )
    except RuntimeError as e:
        logger.warning("get_compose failed for %s/%s: %s", agent_id, container_name, e)
        raise HTTPException(status_code=503, detail="Agent nie odpowiedział. Spróbuj ponownie.")
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
    _validate_ids(agent_id, container_name)
    _check_agent_access(agent_id, info, session)
    _require_online(agent_id)
    try:
        result = await manager.request_from_agent(
            agent_id, action="save_compose",
            params={"container": container_name, "content": body.content},
        )
    except RuntimeError as e:
        logger.warning("save_compose failed for %s/%s: %s", agent_id, container_name, e)
        raise HTTPException(status_code=503, detail="Agent nie odpowiedział. Spróbuj ponownie.")
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
    _validate_ids(agent_id, container_name)
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
        logger.warning("container_action failed for %s/%s: %s", agent_id, container_name, e)
        raise HTTPException(status_code=503, detail="Agent nie odpowiedział. Spróbuj ponownie.")
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
            detail="Agent jest offline.",
        )
