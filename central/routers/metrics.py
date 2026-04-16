"""
routers/metrics.py — Time-series metric snapshots.

GET /api/metrics/{agent_id}/{container_name}?hours=24
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from auth import get_current_user_info
from models import get_allowed_agent_ids, get_metric_snapshots, get_session

router = APIRouter(tags=["metrics"])


@router.get("/api/metrics/{agent_id}/{container_name}")
async def get_metrics(
    agent_id: str,
    container_name: str,
    hours: int = Query(default=24, ge=1, le=168),
    session: Session = Depends(get_session),
    info: dict = Depends(get_current_user_info),
):
    allowed = get_allowed_agent_ids(session, info["username"], info["role"])
    if allowed is not None and agent_id not in allowed:
        raise HTTPException(status_code=403, detail="Brak dostępu do tego serwera.")
    snaps = get_metric_snapshots(session, agent_id, container_name, hours=hours)
    return {
        "agent_id": agent_id,
        "container_name": container_name,
        "hours": hours,
        "points": [
            {
                "t": s.recorded_at.isoformat(),
                "cpu": s.cpu_percent,
                "mem": s.mem_percent,
                "mem_bytes": s.mem_usage_bytes,
                "rx": s.net_rx_bytes,
                "tx": s.net_tx_bytes,
                "blk_r": s.blkio_read_bytes,
                "blk_w": s.blkio_write_bytes,
                "pids": s.pids,
                "status": s.status,
            }
            for s in snaps
        ],
    }
