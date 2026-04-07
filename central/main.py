"""
main.py — DockerMind Central — FastAPI entry point.

Endpoints:
  REST  →  /api/*          (routers/auth, routers/servers, routers/analysis)
  WS    →  /ws/agent       (agent connections)
  WS    →  /ws/dashboard   (browser dashboard live updates)
  Static→  /               (single-file SPA: static/index.html)
"""

import json
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from auth import verify_agent_ws, verify_dashboard_ws
from models import create_db
from routers.alerts import router as alerts_router
from routers.analysis import router as analysis_router
from routers.auth import router as auth_router
from routers.metrics import router as metrics_router
from routers.secrets import router as secrets_router
from routers.servers import router as servers_router
from routers.settings import router as settings_router
from websocket_manager import manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("DockerMind Central starting...")
    create_db()
    manager.start()
    yield
    logger.info("DockerMind Central shutting down...")
    await manager.stop()


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="DockerMind Central",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
)

app.include_router(auth_router)
app.include_router(servers_router)
app.include_router(analysis_router)
app.include_router(alerts_router)
app.include_router(metrics_router)
app.include_router(secrets_router)
app.include_router(settings_router)


# ── WebSocket: Agent ───────────────────────────────────────────────────────────

@app.websocket("/ws/agent")
async def agent_ws(websocket: WebSocket):
    # Validate agent token before accepting
    if not await verify_agent_ws(websocket):
        await websocket.close(code=4003, reason="Nieautoryzowany token agenta.")
        return

    await websocket.accept()
    agent_id: str | None = None

    try:
        # First message must be registration
        raw = await websocket.receive_text()
        import json
        msg = json.loads(raw)

        if msg.get("type") != "register":
            await websocket.close(code=4000, reason="Oczekiwano rejestracji.")
            return

        agent_id = await manager.register_agent(websocket, msg)

        # Send ACK
        await websocket.send_text(json.dumps({
            "type": "ack",
            "message": f"Zarejestrowano jako '{agent_id}'.",
        }))

        # Main receive loop
        while True:
            raw = await websocket.receive_text()
            await manager.handle_agent_message(agent_id, raw)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("Agent WS error (%s): %s", agent_id, e)
    finally:
        if agent_id:
            await manager.handle_agent_disconnect(agent_id)


# ── WebSocket: Dashboard ───────────────────────────────────────────────────────

@app.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket):
    # Validate JWT token (?token=...)
    username = await verify_dashboard_ws(websocket)
    if not username:
        await websocket.close(code=4001, reason="Nieautoryzowany. Wymagany token JWT.")
        return

    await websocket.accept()
    session_id = await manager.connect_dashboard(websocket)

    try:
        # Send current state snapshot to newly connected dashboard
        import json, time
        agents = manager.get_all_agents()
        await websocket.send_text(json.dumps({
            "event": "init",
            "data": {"agents": agents, "timestamp": time.time()},
        }))

        # Keep connection alive; dashboard sends pings, we ignore them
        while True:
            await websocket.receive_text()

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("Dashboard WS error (%s): %s", session_id[:8], e)
    finally:
        await manager.disconnect_dashboard(session_id)


# ── WebSocket: Terminal ────────────────────────────────────────────────────────

@app.websocket("/ws/terminal")
async def terminal_ws(websocket: WebSocket):
    username = await verify_dashboard_ws(websocket)
    if not username:
        await websocket.close(code=4001, reason="Nieautoryzowany.")
        return

    agent_id  = websocket.query_params.get("agent_id", "")
    container = websocket.query_params.get("container", "")
    cols = int(websocket.query_params.get("cols", "220"))
    rows = int(websocket.query_params.get("rows", "50"))

    if not agent_id or not container:
        await websocket.close(code=4000, reason="Brak agent_id lub container.")
        return

    await websocket.accept()
    session_id = str(uuid.uuid4())
    await manager.register_terminal(session_id, websocket)

    try:
        await manager.send_to_agent(agent_id, json.dumps({
            "type": "exec_start",
            "session_id": session_id,
            "container": container,
            "cols": cols,
            "rows": rows,
        }))

        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            t = msg.get("type")
            if t == "input":
                await manager.send_to_agent(agent_id, json.dumps({
                    "type": "exec_input",
                    "session_id": session_id,
                    "data": msg.get("data", ""),
                }))
            elif t == "resize":
                await manager.send_to_agent(agent_id, json.dumps({
                    "type": "exec_resize",
                    "session_id": session_id,
                    "cols": msg.get("cols", 80),
                    "rows": msg.get("rows", 24),
                }))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("Terminal WS error (%s): %s", session_id[:8], e)
    finally:
        await manager.unregister_terminal(session_id)
        try:
            await manager.send_to_agent(agent_id, json.dumps({
                "type": "exec_end",
                "session_id": session_id,
            }))
        except Exception:
            pass


# ── Static SPA ─────────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
async def spa():
    return FileResponse("static/index.html")


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    # All non-API, non-WS routes serve the SPA
    return FileResponse("static/index.html")
