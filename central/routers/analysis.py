"""
routers/analysis.py — AI analysis endpoints.

POST   /api/analyze          trigger analysis (streams via WebSocket, saves to DB)
GET    /api/analyses          list saved analyses
GET    /api/analyses/{id}     single analysis
DELETE /api/analyses/{id}     delete analysis
GET    /api/servers/{agent_id}/containers/{name}/history   chart data
"""

import asyncio
import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session

from auth import get_current_user
from models import (
    Analysis,
    delete_analysis,
    get_analyses,
    get_analysis,
    get_events,
    get_session,
    record_event,
    save_analysis,
)
from websocket_manager import manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analysis"])


# ── Request / response schemas ────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    agent_id: str
    container_name: str


# ── Trigger analysis ──────────────────────────────────────────────────────────

@router.post("/api/analyze")
async def trigger_analysis(
    body: AnalyzeRequest,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
    """
    1. Fetch fresh container snapshot from agent.
    2. Run AI analysis (streaming).
    3. Broadcast tokens to dashboards via WebSocket.
    4. Save completed analysis to DB.
    5. Return saved analysis record.
    """
    agent_id = body.agent_id
    container_name = body.container_name

    if not manager.is_agent_online(agent_id):
        raise HTTPException(status_code=503, detail=f"Agent '{agent_id}' jest offline.")

    # 1. Fetch container snapshot from agent
    try:
        snapshot = await manager.request_from_agent(
            agent_id,
            action="trigger_analysis",
            params={"container": container_name},
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    if not snapshot or "error" in snapshot:
        raise HTTPException(
            status_code=404,
            detail=f"Kontener '{container_name}' nie znaleziony na agencie.",
        )

    # 2. Run AI analysis in background, streaming tokens to dashboards
    #    We run it as a task so the HTTP response returns immediately with
    #    the analysis_id; the SPA listens on WebSocket for streamed tokens.
    analysis_id_holder: list[int] = []

    async def _run():
        from ai.analyzer import analyze_container
        try:
            analysis = await analyze_container(
                agent_id=agent_id,
                snapshot=snapshot,
                broadcast_fn=manager.broadcast_to_dashboards,
            )
            saved = save_analysis(session, analysis)
            analysis_id_holder.append(saved.id)
            await manager.broadcast_to_dashboards("analysis_done", {
                "agent_id": agent_id,
                "container_name": container_name,
                "analysis_id": saved.id,
                "risk_level": saved.risk_level,
            })

            # Record crash event if restart_count > 0 or exit_code != 0
            if snapshot.get("restart_count", 0) > 0 or snapshot.get("exit_code", 0) != 0:
                record_event(
                    session,
                    agent_id=agent_id,
                    container_name=container_name,
                    event_type="crash" if snapshot.get("exit_code", 0) != 0 else "restart",
                    exit_code=snapshot.get("exit_code", 0),
                    restart_count=snapshot.get("restart_count", 0),
                    cpu_percent=snapshot.get("cpu_percent", 0.0),
                    mem_percent=snapshot.get("memory", {}).get("percent", 0.0),
                )
        except Exception as e:
            logger.error("Analysis failed for %s/%s: %s", agent_id, container_name, e)
            await manager.broadcast_to_dashboards("analysis_error", {
                "agent_id": agent_id,
                "container_name": container_name,
                "error": str(e),
            })

    asyncio.create_task(_run())

    return {
        "status": "started",
        "agent_id": agent_id,
        "container_name": container_name,
        "message": "Analiza uruchomiona. Wyniki są przesyłane przez WebSocket.",
    }


# ── Saved analyses ────────────────────────────────────────────────────────────

@router.get("/api/analyses")
async def list_analyses(
    agent_id: str = Query(default=None),
    container_name: str = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
    analyses = get_analyses(session, agent_id=agent_id, container_name=container_name, limit=limit)
    return [_analysis_summary(a) for a in analyses]


@router.get("/api/analyses/{analysis_id}")
async def get_single_analysis(
    analysis_id: int,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
    obj = get_analysis(session, analysis_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Analiza nie znaleziona.")
    return _analysis_full(obj)


@router.delete("/api/analyses/{analysis_id}")
async def remove_analysis(
    analysis_id: int,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
    ok = delete_analysis(session, analysis_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Analiza nie znaleziona.")
    return {"deleted": analysis_id}


# ── History / chart data ──────────────────────────────────────────────────────

@router.get("/api/servers/{agent_id}/containers/{container_name}/history")
async def get_history(
    agent_id: str,
    container_name: str,
    days: int = Query(default=7, ge=1, le=30),
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
    events = get_events(session, agent_id, container_name, days=days)
    return {
        "agent_id": agent_id,
        "container_name": container_name,
        "days": days,
        "events": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "exit_code": e.exit_code,
                "restart_count": e.restart_count,
                "cpu_percent": e.cpu_percent,
                "mem_percent": e.mem_percent,
                "occurred_at": e.occurred_at.isoformat(),
            }
            for e in events
        ],
    }


# ── Serialization helpers ──────────────────────────────────────────────────────

def _analysis_summary(a: Analysis) -> dict:
    first_line = a.content.splitlines()[0][:120] if a.content else ""
    return {
        "id": a.id,
        "agent_id": a.agent_id,
        "container_name": a.container_name,
        "container_image": a.container_image,
        "risk_level": a.risk_level,
        "first_line": first_line,
        "created_at": a.created_at.isoformat(),
    }


def _analysis_full(a: Analysis) -> dict:
    return {
        "id": a.id,
        "agent_id": a.agent_id,
        "container_name": a.container_name,
        "container_image": a.container_image,
        "risk_level": a.risk_level,
        "content": a.content,
        "cpu_percent": a.cpu_percent,
        "mem_percent": a.mem_percent,
        "restart_count": a.restart_count,
        "exit_code": a.exit_code,
        "last_crash": a.last_crash,
        "created_at": a.created_at.isoformat(),
    }
