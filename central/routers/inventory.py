import json
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from auth import get_current_user_info
from models import AlertEvent, InventoryRule, get_session, log_audit
from websocket_manager import manager, _PROCESS_LOCK
from inventory import evaluate_inventory

router = APIRouter(tags=['inventory'])


async def refresh_inventory(session):
    """Apply edits to the latest known online snapshots immediately."""
    for agent in manager.get_all_agents():
        if not agent['online']:
            continue
        with _PROCESS_LOCK:
            events = evaluate_inventory(session, agent['agent_id'],
                                        manager.get_agent_containers(agent['agent_id']) or [])
        for event in events:
            await manager.broadcast_to_dashboards(event.pop('event', 'alert_triggered'), event)
    await manager.broadcast_to_dashboards('inventory_rules_changed', {})


class RuleBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    field: str = 'image'
    patterns: str = Field(min_length=1, max_length=2000)
    agent_name: str = Field(default='', max_length=120)
    group_name: str = Field(default='', max_length=80)
    expected_tag: str = Field(default='', max_length=128)
    enabled: bool = True


def checked(body, info):
    if info['role'] != 'admin':
        raise HTTPException(403, 'Wymagana rola administrator.')
    data = body.model_dump()
    for key in ('name', 'patterns', 'agent_name', 'group_name', 'expected_tag'):
        data[key] = data[key].strip()
    if not data['name'] or not data['patterns'] or body.field not in ('name', 'image'):
        raise HTTPException(400, 'Wymagana nazwa, fragmenty nazw i poprawne pole dopasowania.')
    if not data['group_name'] and not data['expected_tag'] and not data['agent_name']:
        raise HTTPException(400, 'Podaj grupę lub wymagany tag.')
    if data['expected_tag'] and not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', data['expected_tag']):
        raise HTTPException(400, 'Nieprawidłowy tag obrazu.')
    return data


@router.get('/api/inventory-rules')
def list_rules(session: Session = Depends(get_session), info: dict = Depends(get_current_user_info)):
    return session.exec(select(InventoryRule).order_by(InventoryRule.id)).all()


@router.post('/api/inventory-rules', status_code=201)
async def create_rule(body: RuleBody, session: Session = Depends(get_session), info: dict = Depends(get_current_user_info)):
    rule = InventoryRule(**checked(body, info))
    session.add(rule)
    session.commit()
    session.refresh(rule)
    result = rule.model_dump()
    log_audit(session, 'inventory_rule_created', username=info['username'], detail=json.dumps({'id': rule.id}))
    await refresh_inventory(session)
    return result


def resolve_events(session, rule_id):
    for event in session.exec(select(AlertEvent).where(AlertEvent.rule_id == -rule_id,
            AlertEvent.metric == 'image_tag', AlertEvent.status.in_(['active', 'acknowledged']))).all():
        event.status = 'resolved'
        session.add(event)


@router.put('/api/inventory-rules/{rule_id}')
async def update_rule(rule_id: int, body: RuleBody, session: Session = Depends(get_session), info: dict = Depends(get_current_user_info)):
    data = checked(body, info)
    rule = session.get(InventoryRule, rule_id)
    if not rule:
        raise HTTPException(404, 'Reguła nie istnieje.')
    for key, value in data.items():
        setattr(rule, key, value)
    resolve_events(session, rule_id)
    session.add(rule)
    session.commit()
    session.refresh(rule)
    result = rule.model_dump()
    log_audit(session, 'inventory_rule_updated', username=info['username'], detail=json.dumps({'id': rule.id}))
    await refresh_inventory(session)
    return result


@router.delete('/api/inventory-rules/{rule_id}')
async def delete_rule(rule_id: int, session: Session = Depends(get_session), info: dict = Depends(get_current_user_info)):
    if info['role'] != 'admin':
        raise HTTPException(403, 'Wymagana rola administrator.')
    rule = session.get(InventoryRule, rule_id)
    if not rule:
        raise HTTPException(404, 'Reguła nie istnieje.')
    resolve_events(session, rule_id)
    session.delete(rule)
    session.commit()
    log_audit(session, 'inventory_rule_deleted', username=info['username'], detail=json.dumps({'id': rule_id}))
    await refresh_inventory(session)
    return {'deleted': rule_id}
