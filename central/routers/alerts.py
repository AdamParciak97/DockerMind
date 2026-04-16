"""
routers/alerts.py — Alert rules and events.

GET    /api/alerts                       list rules
POST   /api/alerts                       create rule
DELETE /api/alerts/{id}                  delete rule
PUT    /api/alerts/{id}/toggle           enable / disable
GET    /api/alert-events                 list triggered events
POST   /api/alert-events/{id}/ack        acknowledge
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from auth import get_current_user_info
from models import AlertEvent, AlertRule, get_alert_events, get_alert_rules, get_allowed_agent_ids, get_session

router = APIRouter(tags=["alerts"])

VALID_METRICS = {"cpu_percent", "mem_percent", "restart_count", "status_stopped"}


# ── Request schemas ───────────────────────────────────────────────────────────

class AlertRuleCreate(BaseModel):
    agent_id: str
    container_name: str   # "*" = all containers on this agent
    metric: str
    threshold: float
    min_duration: int = 0  # minutes sustained; 0 = immediate


# ── Alert rules ───────────────────────────────────────────────────────────────

@router.get("/api/alerts")
async def list_rules(
    agent_id: Optional[str] = Query(default=None),
    container_name: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    allowed = get_allowed_agent_ids(session, info["username"], info["role"])
    rules = get_alert_rules(session, agent_id=agent_id, container_name=container_name)
    if allowed is not None:
        rules = [r for r in rules if r.agent_id in allowed]
    return [_rule_dict(r) for r in rules]


@router.post("/api/alerts")
async def create_rule(
    body: AlertRuleCreate,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    allowed = get_allowed_agent_ids(session, info["username"], info["role"])
    if allowed is not None and body.agent_id not in allowed:
        raise HTTPException(status_code=403, detail="Brak dostępu do tego serwera.")
    if body.metric not in VALID_METRICS:
        raise HTTPException(status_code=400, detail=f"Nieprawidłowa metryka: {body.metric}")
    rule = AlertRule(
        agent_id=body.agent_id,
        container_name=body.container_name,
        metric=body.metric,
        threshold=body.threshold,
        min_duration=max(0, body.min_duration),
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return _rule_dict(rule)


@router.delete("/api/alerts/{rule_id}")
async def delete_rule(
    rule_id: int,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    rule = session.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Reguła nie znaleziona.")
    allowed = get_allowed_agent_ids(session, info["username"], info["role"])
    if allowed is not None and rule.agent_id not in allowed:
        raise HTTPException(status_code=403, detail="Brak dostępu do tego serwera.")
    session.delete(rule)
    session.commit()
    return {"deleted": rule_id}


@router.put("/api/alerts/{rule_id}/toggle")
async def toggle_rule(
    rule_id: int,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    rule = session.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Reguła nie znaleziona.")
    allowed = get_allowed_agent_ids(session, info["username"], info["role"])
    if allowed is not None and rule.agent_id not in allowed:
        raise HTTPException(status_code=403, detail="Brak dostępu do tego serwera.")
    rule.enabled = not rule.enabled
    session.add(rule)
    session.commit()
    return _rule_dict(rule)


# ── Alert events ──────────────────────────────────────────────────────────────

@router.get("/api/alert-events")
async def list_events(
    agent_id: Optional[str] = Query(default=None),
    container_name: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    allowed = get_allowed_agent_ids(session, info["username"], info["role"])
    events = get_alert_events(
        session,
        agent_id=agent_id,
        container_name=container_name,
        status=status,
        limit=limit,
    )
    if allowed is not None:
        events = [e for e in events if e.agent_id in allowed]
    return [_event_dict(e) for e in events]


@router.post("/api/alert-events/{event_id}/ack")
async def acknowledge_event(
    event_id: int,
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    evt = session.get(AlertEvent, event_id)
    if not evt:
        raise HTTPException(status_code=404, detail="Zdarzenie nie znalezione.")
    allowed = get_allowed_agent_ids(session, info["username"], info["role"])
    if allowed is not None and evt.agent_id not in allowed:
        raise HTTPException(status_code=403, detail="Brak dostępu do tego serwera.")
    evt.status = "acknowledged"
    evt.ack_at = datetime.now(timezone.utc)
    session.add(evt)
    session.commit()
    return _event_dict(evt)


# ── Serializers ───────────────────────────────────────────────────────────────

_METRIC_LABELS = {
    "cpu_percent": "CPU %",
    "mem_percent": "RAM %",
    "restart_count": "Restarty",
    "status_stopped": "Kontener zatrzymany",
}


def _rule_dict(r: AlertRule) -> dict:
    return {
        "id": r.id,
        "agent_id": r.agent_id,
        "container_name": r.container_name,
        "metric": r.metric,
        "metric_label": _METRIC_LABELS.get(r.metric, r.metric),
        "threshold": r.threshold,
        "min_duration": r.min_duration,
        "enabled": r.enabled,
        "created_at": r.created_at.isoformat(),
    }


def _event_dict(e: AlertEvent) -> dict:
    return {
        "id": e.id,
        "rule_id": e.rule_id,
        "agent_id": e.agent_id,
        "container_name": e.container_name,
        "metric": e.metric,
        "metric_label": _METRIC_LABELS.get(e.metric, e.metric),
        "value": e.value,
        "threshold": e.threshold,
        "status": e.status,
        "triggered_at": e.triggered_at.isoformat(),
        "ack_at": e.ack_at.isoformat() if e.ack_at else None,
    }
