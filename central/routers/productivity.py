"""Command history/templates/schedules, dry-run, health and fleet exports."""
import csv
import io
import json
import re
from datetime import datetime, timezone
import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from auth import get_current_user_info
from models import (CommandHistory, CommandSchedule, CommandTemplate, decrypt_secret,
                    encrypt_secret, get_allowed_agent_ids, get_session, log_audit)
from websocket_manager import agent_supports_host_commands, manager

router = APIRouter(tags=['productivity'])
_CRON_RE = re.compile(r'^[0-9*/?,\- ]{1,80}$')

@router.get('/api/ai-health')
async def ai_health(info: dict = Depends(get_current_user_info)):
    """Return a live reachability check for the configured OpenAI-compatible endpoint."""
    from config import settings
    import httpx
    base = settings.AI_BASE_URL.rstrip('/')
    headers = {'Authorization': f'Bearer {settings.AI_API_TOKEN}'} if settings.AI_API_TOKEN else {}
    try:
        async with httpx.AsyncClient(timeout=3.0, verify=settings.AI_VERIFY_SSL, headers=headers) as client:
            response = await client.get(base + '/models')
        return {'online': response.is_success, 'configured': bool(settings.AI_API_TOKEN), 'model': settings.AI_MODEL,
                'status_code': response.status_code}
    except Exception:
        return {'online': False, 'configured': bool(settings.AI_API_TOKEN), 'model': settings.AI_MODEL, 'status_code': None}


def _cron_matches(expr, now):
    parts = expr.split()
    if len(parts) != 5: return False
    values = [now.minute, now.hour, now.day, now.month, (now.weekday() + 1) % 7]
    for token, value in zip(parts, values):
        if token in ('*', '?'): continue
        allowed = set()
        for item in token.split(','):
            if item.isdigit(): allowed.add(int(item))
            elif '/' in item:
                base, step = item.split('/', 1)
                if step.isdigit() and (base == '*' or base.isdigit()) and value % int(step) == 0: continue
        if allowed and value not in allowed: return False
        if not allowed and token not in ('*', '?'): return False
    return True


async def schedule_tick():
    """Run due schedules once per minute; called by central lifespan."""
    from models import engine
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    with Session(engine) as session:
        rows = session.exec(select(CommandSchedule).where(CommandSchedule.enabled == True)).all()
        for row in rows:
            if row.last_run_at and row.last_run_at.replace(second=0, microsecond=0) >= now: continue
            if not _cron_matches(row.cron, now): continue
            for agent_id in json.loads(row.targets_json):
                agent = manager.get_agent(agent_id)
                caps = (agent or {}).get('info', {}).get('capabilities', {})
                if not agent or not agent['online'] or not agent_supports_host_commands(agent.get('info')): continue
                try:
                    await manager.request_from_agent(agent_id, 'host_command', {'command': decrypt_secret(row.command_enc), 'timeout': 120}, timeout=130)
                except RuntimeError: pass
            row.last_run_at = now; session.add(row)
        session.commit()


def admin(info):
    if info['role'] != 'admin':
        raise HTTPException(403, 'Wymagana rola administratora.')


class TemplateBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    command: str = Field(min_length=1, max_length=16000)
    timeout: int = Field(default=30, ge=1, le=120, strict=True)
    allowed_roles: str = 'admin'


class ScheduleBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    command: str = Field(min_length=1, max_length=16000)
    cron: str = Field(min_length=1, max_length=80)
    targets: list[str] = Field(default_factory=list, max_length=200)
    enabled: bool = True

class DryRunBody(BaseModel):
    command: str = Field(min_length=1, max_length=16000)
    targets: list[str] = Field(default_factory=list, max_length=200)


def _server_targets(session, info):
    allowed = get_allowed_agent_ids(session, info['username'], info['role'])
    return [a for a in manager.get_agents_filtered(allowed)]


@router.get('/api/agent-health')
async def agent_health(session: Session = Depends(get_session), info: dict = Depends(get_current_user_info)):
    result = []
    for agent in _server_targets(session, info):
        capabilities = agent.get('info', {}).get('capabilities', {})
        result.append({
            'agent_id': agent['agent_id'], 'name': agent.get('display_name') or agent['info'].get('agent_name', agent['agent_id']),
            'online': agent['online'], 'last_seen': agent['last_seen'], 'capabilities': capabilities,
            'protocol': capabilities.get('command_protocol', 0),
            'command_ready': agent['online'] and agent_supports_host_commands(agent.get('info')),
            'containers': agent.get('container_count', 0), 'docker_version': agent['info'].get('docker_version', ''),
        })
    return result


@router.post('/api/commands/dry-run')
async def command_dry_run(body: DryRunBody, session: Session = Depends(get_session), info: dict = Depends(get_current_user_info)):
    admin(info)
    targets = set(body.targets)
    agents = [a for a in _server_targets(session, info) if not targets or a['agent_id'] in targets]
    return {'command': body.command, 'targets': [{
        'agent_id': a['agent_id'], 'name': a.get('display_name') or a['info'].get('agent_name', a['agent_id']),
        'online': a['online'], 'reason': '' if a['online'] and agent_supports_host_commands(a.get('info')) else 'Agent nie obsługuje komend hosta',
    } for a in agents]}


@router.get('/api/command-history')
async def command_history(limit: int = Query(50, ge=1, le=200), session: Session = Depends(get_session), info: dict = Depends(get_current_user_info)):
    rows = session.exec(select(CommandHistory).where(CommandHistory.username == info['username']).order_by(CommandHistory.created_at.desc()).limit(limit)).all()
    return [{'id': r.id, 'command': decrypt_secret(r.command_enc), 'command_hash': r.command_hash, 'timeout': r.timeout, 'targets': json.loads(r.targets_json), 'status': r.status, 'created_at': r.created_at.isoformat()} for r in rows]


@router.post('/api/command-history/{history_id}/reuse')
async def reuse_command(history_id: int, session: Session = Depends(get_session), info: dict = Depends(get_current_user_info)):
    row = session.get(CommandHistory, history_id)
    if not row or row.username != info['username']:
        raise HTTPException(404, 'Historia polecenia nie istnieje.')
    return {'command': decrypt_secret(row.command_enc), 'timeout': row.timeout, 'targets': json.loads(row.targets_json)}


@router.get('/api/command-templates')
async def templates(session: Session = Depends(get_session), info: dict = Depends(get_current_user_info)):
    rows = session.exec(select(CommandTemplate).order_by(CommandTemplate.name)).all()
    return [{'id': r.id, 'name': r.name, 'command': decrypt_secret(r.command_enc), 'timeout': r.timeout, 'allowed_roles': r.allowed_roles} for r in rows if info['role'] in r.allowed_roles.split(',')]


@router.post('/api/command-templates', status_code=201)
async def create_template(body: TemplateBody, session: Session = Depends(get_session), info: dict = Depends(get_current_user_info)):
    admin(info)
    roles = ','.join(sorted(set(x.strip() for x in body.allowed_roles.split(',') if x.strip()) & {'admin', 'user'})) or 'admin'
    row = CommandTemplate(name=body.name.strip(), command_enc=encrypt_secret(body.command), timeout=body.timeout, allowed_roles=roles, created_by=info['username'])
    session.add(row); session.commit(); session.refresh(row)
    log_audit(session, 'command_template_created', username=info['username'], detail=json.dumps({'id': row.id, 'name': row.name}))
    return {'id': row.id, 'name': row.name, 'command': body.command, 'timeout': row.timeout, 'allowed_roles': roles}


@router.delete('/api/command-templates/{template_id}')
async def delete_template(template_id: int, session: Session = Depends(get_session), info: dict = Depends(get_current_user_info)):
    admin(info); row = session.get(CommandTemplate, template_id)
    if not row: raise HTTPException(404, 'Szablon nie istnieje.')
    session.delete(row); session.commit(); return {'deleted': template_id}


@router.get('/api/command-schedules')
async def schedules(session: Session = Depends(get_session), info: dict = Depends(get_current_user_info)):
    admin(info); rows = session.exec(select(CommandSchedule).order_by(CommandSchedule.created_at.desc())).all()
    return [{'id': r.id, 'name': r.name, 'command': decrypt_secret(r.command_enc), 'cron': r.cron, 'targets': json.loads(r.targets_json), 'enabled': r.enabled, 'last_run_at': r.last_run_at, 'next_run_at': r.next_run_at} for r in rows]


@router.post('/api/command-schedules', status_code=201)
async def create_schedule(body: ScheduleBody, session: Session = Depends(get_session), info: dict = Depends(get_current_user_info)):
    admin(info)
    if not _CRON_RE.fullmatch(body.cron.strip()): raise HTTPException(400, 'Nieprawidłowy zapis harmonogramu cron.')
    available = {a['agent_id'] for a in _server_targets(session, info)}
    if any(target not in available for target in body.targets): raise HTTPException(400, 'Harmonogram zawiera niedostępny serwer.')
    row = CommandSchedule(name=body.name.strip(), command_enc=encrypt_secret(body.command), cron=body.cron.strip(), targets_json=json.dumps(body.targets), enabled=body.enabled, created_by=info['username'])
    session.add(row); session.commit(); session.refresh(row)
    return {'id': row.id, 'name': row.name, 'command': body.command, 'cron': row.cron, 'targets': body.targets, 'enabled': row.enabled, 'last_run_at': None, 'next_run_at': None}


@router.delete('/api/command-schedules/{schedule_id}')
async def delete_schedule(schedule_id: int, session: Session = Depends(get_session), info: dict = Depends(get_current_user_info)):
    admin(info); row = session.get(CommandSchedule, schedule_id)
    if not row: raise HTTPException(404, 'Harmonogram nie istnieje.')
    session.delete(row); session.commit(); return {'deleted': schedule_id}


@router.get('/api/reports/fleet.csv')
async def fleet_csv(session: Session = Depends(get_session), info: dict = Depends(get_current_user_info)):
    output = io.StringIO(); writer = csv.writer(output); writer.writerow(['agent_id', 'name', 'online', 'last_seen', 'containers', 'docker_version', 'command_protocol', 'command_ready'])
    for agent in _server_targets(session, info):
        caps = agent['info'].get('capabilities', {})
        writer.writerow([agent['agent_id'], agent.get('display_name') or agent['info'].get('agent_name', ''), agent['online'], agent['last_seen'], agent.get('container_count', 0), agent['info'].get('docker_version', ''), caps.get('command_protocol', 0), caps.get('host_commands', False)])
    return StreamingResponse(iter([output.getvalue()]), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename=dockermind.csv'})

@router.get('/api/reports/fleet.pdf')
async def fleet_pdf(session: Session = Depends(get_session), info: dict = Depends(get_current_user_info)):
    try:
        from fpdf import FPDF
    except ImportError:
        raise HTTPException(503, 'Eksport PDF wymaga biblioteki fpdf2.')
    pdf = FPDF(); pdf.set_auto_page_break(True, 12); pdf.add_page(); pdf.set_font('Helvetica', size=14)
    pdf.cell(0, 10, 'DockerMind', ln=True)
    pdf.set_font('Helvetica', size=8)
    pdf.cell(0, 7, datetime.now(timezone.utc).strftime('Wygenerowano: %Y-%m-%d %H:%M UTC'), ln=True)
    for agent in _server_targets(session, info):
        caps = agent['info'].get('capabilities', {})
        name = agent.get('display_name') or agent['info'].get('agent_name', agent['agent_id'])
        line = f"{name} | {'online' if agent['online'] else 'offline'} | kontenery: {agent.get('container_count', 0)} | Docker: {agent['info'].get('docker_version', '')} | komendy: {'tak' if caps.get('host_commands') else 'nie'}"
        pdf.multi_cell(0, 6, line)
    return Response(bytes(pdf.output()), media_type='application/pdf', headers={'Content-Disposition': 'attachment; filename=dockermind.pdf'})
