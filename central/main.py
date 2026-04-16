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
import secrets
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from auth import verify_agent_ws, verify_dashboard_ws
from config import warn_insecure_defaults
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
    warn_insecure_defaults()
    create_db()
    manager.start()
    yield
    logger.info("DockerMind Central shutting down...")
    await manager.stop()


# ── App ────────────────────────────────────────────────────────────────────────

# ── Security headers middleware ────────────────────────────────────────────────

_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self' data:; "
    "img-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "frame-ancestors 'none';"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        # Prevent caching of API responses (tokens, sensitive data)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response


_CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_CSRF_EXEMPT = {"/api/auth/login"}
_CSRF_EXEMPT_PREFIXES = ("/ws/", "/static/")

_CSRF_COOKIE_OPTS = dict(
    key="csrf_token",
    httponly=False,    # JS must read it for the double-submit pattern
    samesite="lax",
    secure=True,
    path="/",
)


class CSRFMiddleware(BaseHTTPMiddleware):
    """
    Double-submit cookie CSRF protection.
    JS reads the csrf_token cookie and sends it as X-CSRF-Token header.
    Middleware checks they match on all state-changing requests.
    """

    async def dispatch(self, request: Request, call_next):
        if (
            request.method not in _CSRF_SAFE_METHODS
            and request.url.path not in _CSRF_EXEMPT
            and not any(request.url.path.startswith(p) for p in _CSRF_EXEMPT_PREFIXES)
        ):
            cookie_token = request.cookies.get("csrf_token", "")
            header_token = request.headers.get("x-csrf-token", "")
            if not cookie_token or not secrets.compare_digest(cookie_token, header_token):
                return JSONResponse(
                    {"detail": "Nieprawidłowy token CSRF. Odśwież stronę."},
                    status_code=403,
                )
        return await call_next(request)


app = FastAPI(
    title="DockerMind Central",
    version="1.2.0",
    lifespan=lifespan,
    docs_url=None,       # Wyłączone w produkcji — ujawnia strukturę API
    redoc_url=None,
    openapi_url=None,    # Wyłącza też /openapi.json
)

app.add_middleware(CSRFMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

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
    auth = await verify_dashboard_ws(websocket)
    if not auth:
        await websocket.close(code=4001, reason="Nieautoryzowany. Wymagany token JWT.")
        return
    username, role = auth

    # Compute allowed agents for this user (None = all)
    import json, time
    from models import get_allowed_agent_ids, get_session as _get_session
    from sqlmodel import Session as _Session
    from models import engine as _engine
    with _Session(_engine) as _sess:
        allowed_agents = get_allowed_agent_ids(_sess, username, role)

    await websocket.accept()
    session_id = await manager.connect_dashboard(websocket, username, role, allowed_agents)

    try:
        # Send current state snapshot to newly connected dashboard (filtered)
        agents = manager.get_agents_filtered(allowed_agents)
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
    auth = await verify_dashboard_ws(websocket)
    if not auth:
        await websocket.close(code=4001, reason="Nieautoryzowany.")
        return
    username, role = auth

    agent_id  = websocket.query_params.get("agent_id", "")
    container = websocket.query_params.get("container", "")

    # Kontrola dostępu do agenta
    from models import get_allowed_agent_ids, engine as _engine
    from sqlmodel import Session as _Session
    with _Session(_engine) as _sess:
        allowed = get_allowed_agent_ids(_sess, username, role)
    if allowed is not None and agent_id not in allowed:
        await websocket.close(code=4003, reason="Brak dostępu do tego serwera.")
        return
    try:
        cols = max(10, min(500, int(websocket.query_params.get("cols", "220"))))
        rows = max(5,  min(200, int(websocket.query_params.get("rows", "50"))))
    except ValueError:
        cols, rows = 220, 50

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
    response = FileResponse("static/index.html")
    response.set_cookie(value=secrets.token_hex(32), max_age=86400, **_CSRF_COOKIE_OPTS)
    return response


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    # All non-API, non-WS routes serve the SPA
    response = FileResponse("static/index.html")
    response.set_cookie(value=secrets.token_hex(32), max_age=86400, **_CSRF_COOKIE_OPTS)
    return response
