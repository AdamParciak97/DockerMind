"""
routers/metrics.py — Time-series metric snapshots.

GET /api/metrics/{agent_id}/{container_name}?hours=24
"""

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from auth import get_current_user
from models import get_metric_snapshots, get_session

router = APIRouter(tags=["metrics"])


@router.get("/api/metrics/{agent_id}/{container_name}")
async def get_metrics(
    agent_id: str,
    container_name: str,
    hours: int = Query(default=24, ge=1, le=168),
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
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
