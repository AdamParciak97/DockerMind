"""
websocket_manager.py — Central WebSocket hub for DockerMind.

Manages:
  - Agent connections: registration, data ingestion, offline detection
  - Dashboard connections: live event broadcasting
  - On-demand request routing: central → agent → central → dashboard
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Optional

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

# Agent marked offline after this many seconds without a ping/data message
AGENT_OFFLINE_TIMEOUT = 60

# How often the watchdog checks for timed-out agents (seconds)
WATCHDOG_INTERVAL = 15

# Max seconds to wait for an agent to respond to an on-demand request
REQUEST_TIMEOUT = 30


class AgentConnection:
    """Holds state for one connected agent."""

    __slots__ = (
        "ws", "agent_id", "info", "last_seen",
        "containers", "pending_requests",
    )

    def __init__(self, ws: WebSocket, agent_id: str, info: dict):
        self.ws = ws
        self.agent_id = agent_id
        self.info = info                      # registration payload
        self.last_seen = time.time()
        self.containers: list[dict] = []      # latest container snapshot
        self.pending_requests: dict[str, asyncio.Future] = {}


class WebSocketManager:
    def __init__(self):
        # agent_id → AgentConnection
        self._agents: dict[str, AgentConnection] = {}
        # session_id → WebSocket (dashboard browsers)
        self._dashboards: dict[str, WebSocket] = {}
        # lock for mutating the dicts
        self._lock = asyncio.Lock()
        # background watchdog task handle
        self._watchdog_task: Optional[asyncio.Task] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start background watchdog. Call from FastAPI startup event."""
        self._watchdog_task = asyncio.create_task(self._watchdog())
        logger.info("WebSocketManager started.")

    async def stop(self) -> None:
        """Cancel watchdog. Call from FastAPI shutdown event."""
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass

    # ── Agent registration / disconnect ───────────────────────────────────────

    async def register_agent(self, ws: WebSocket, info: dict) -> str:
        """
        Register a newly connected agent.
        agent_id is derived from agent_name so the same server always gets
        the same ID (enables reconnect without ghost entries).
        Returns agent_id.
        """
        agent_name = info.get("agent_name") or info.get("hostname", "unknown")
        agent_id = _slug(agent_name)

        conn = AgentConnection(ws=ws, agent_id=agent_id, info=info)

        async with self._lock:
            old = self._agents.get(agent_id)
            if old:
                # Stale entry from a previous connection — resolve pending futures
                for fut in old.pending_requests.values():
                    if not fut.done():
                        fut.set_exception(RuntimeError("Agent reconnected, old connection dropped."))
            self._agents[agent_id] = conn

        logger.info("Agent registered: %s (%s)", agent_id, info.get("ip", "?"))
        await self.broadcast_to_dashboards("agent_online", {
            "agent_id": agent_id,
            "info": info,
        })
        return agent_id

    async def handle_agent_disconnect(self, agent_id: str) -> None:
        async with self._lock:
            conn = self._agents.get(agent_id)
            if conn:
                for fut in conn.pending_requests.values():
                    if not fut.done():
                        fut.set_exception(RuntimeError("Agent disconnected."))
                # Keep entry but mark offline (ws=None) so dashboard knows
                conn.ws = None

        logger.info("Agent disconnected: %s", agent_id)
        await self.broadcast_to_dashboards("agent_offline", {"agent_id": agent_id})

    # ── Agent data ingestion ───────────────────────────────────────────────────

    async def handle_agent_message(self, agent_id: str, raw: str) -> None:
        """Process any message arriving from an agent WebSocket."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Agent %s sent invalid JSON.", agent_id)
            return

        async with self._lock:
            conn = self._agents.get(agent_id)

        if conn is None:
            return

        conn.last_seen = time.time()
        msg_type = msg.get("type")

        if msg_type == "data":
            await self._ingest_data(conn, msg)

        elif msg_type == "response":
            await self._resolve_request(conn, msg)

        elif msg_type == "error":
            request_id = msg.get("request_id")
            if request_id and request_id in conn.pending_requests:
                fut = conn.pending_requests.pop(request_id)
                if not fut.done():
                    fut.set_exception(RuntimeError(msg.get("message", "Agent error")))

        else:
            logger.debug("Agent %s: unhandled message type '%s'", agent_id, msg_type)

    async def _ingest_data(self, conn: AgentConnection, msg: dict) -> None:
        """Store latest container snapshot and push to dashboards."""
        containers = msg.get("containers", [])
        conn.containers = containers

        # Strip raw logs before broadcasting to dashboards
        # (logs are fetched on-demand; sending 100 lines every 30s is wasteful)
        slim_containers = [
            {k: v for k, v in c.items() if k not in ("logs", "compose")}
            for c in containers
        ]

        await self.broadcast_to_dashboards("agent_data", {
            "agent_id": conn.agent_id,
            "timestamp": msg.get("timestamp", time.time()),
            "containers": slim_containers,
        })

    async def _resolve_request(self, conn: AgentConnection, msg: dict) -> None:
        request_id = msg.get("request_id")
        if not request_id:
            return
        fut = conn.pending_requests.pop(request_id, None)
        if fut and not fut.done():
            fut.set_result(msg.get("data"))

    # ── On-demand requests (central → agent) ──────────────────────────────────

    async def request_from_agent(
        self,
        agent_id: str,
        action: str,
        params: Optional[dict] = None,
    ) -> Any:
        """
        Send an on-demand request to an agent and await its response.
        Raises RuntimeError on timeout or if agent is offline.
        """
        async with self._lock:
            conn = self._agents.get(agent_id)

        if conn is None or conn.ws is None:
            raise RuntimeError(f"Agent '{agent_id}' nie jest podłączony.")

        request_id = str(uuid.uuid4())
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()

        conn.pending_requests[request_id] = fut

        payload = {
            "type": "request",
            "request_id": request_id,
            "action": action,
            "params": params or {},
        }
        try:
            await conn.ws.send_text(json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            conn.pending_requests.pop(request_id, None)
            raise RuntimeError(f"Błąd wysyłania żądania do agenta: {e}") from e

        try:
            result = await asyncio.wait_for(fut, timeout=REQUEST_TIMEOUT)
            return result
        except asyncio.TimeoutError:
            conn.pending_requests.pop(request_id, None)
            raise RuntimeError(
                f"Agent '{agent_id}' nie odpowiedział w ciągu {REQUEST_TIMEOUT}s."
            )

    # ── Dashboard connections ──────────────────────────────────────────────────

    async def connect_dashboard(self, ws: WebSocket) -> str:
        session_id = str(uuid.uuid4())
        async with self._lock:
            self._dashboards[session_id] = ws
        logger.info("Dashboard connected: %s", session_id[:8])
        return session_id

    async def disconnect_dashboard(self, session_id: str) -> None:
        async with self._lock:
            self._dashboards.pop(session_id, None)
        logger.info("Dashboard disconnected: %s", session_id[:8])

    async def broadcast_to_dashboards(self, event: str, data: dict) -> None:
        """Send event to all connected dashboard WebSockets. Drop dead connections."""
        if not self._dashboards:
            return

        message = json.dumps(
            {"event": event, "data": data}, ensure_ascii=False, default=str
        )

        dead: list[str] = []
        async with self._lock:
            snapshot = dict(self._dashboards)

        for session_id, ws in snapshot.items():
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(session_id)

        if dead:
            async with self._lock:
                for sid in dead:
                    self._dashboards.pop(sid, None)

    # ── State queries (for REST API) ───────────────────────────────────────────

    def get_all_agents(self) -> list[dict]:
        """Return summary list of all known agents."""
        result = []
        now = time.time()
        for agent_id, conn in self._agents.items():
            online = conn.ws is not None and (now - conn.last_seen) < AGENT_OFFLINE_TIMEOUT
            result.append({
                "agent_id": agent_id,
                "online": online,
                "last_seen": conn.last_seen,
                "info": conn.info,
                "container_count": len(conn.containers),
            })
        return result

    def get_agent(self, agent_id: str) -> Optional[dict]:
        conn = self._agents.get(agent_id)
        if not conn:
            return None
        now = time.time()
        online = conn.ws is not None and (now - conn.last_seen) < AGENT_OFFLINE_TIMEOUT
        return {
            "agent_id": agent_id,
            "online": online,
            "last_seen": conn.last_seen,
            "info": conn.info,
            "containers": conn.containers,
        }

    def get_agent_containers(self, agent_id: str) -> Optional[list[dict]]:
        conn = self._agents.get(agent_id)
        return conn.containers if conn else None

    def is_agent_online(self, agent_id: str) -> bool:
        conn = self._agents.get(agent_id)
        if not conn or conn.ws is None:
            return False
        return (time.time() - conn.last_seen) < AGENT_OFFLINE_TIMEOUT

    # ── Watchdog ───────────────────────────────────────────────────────────────

    async def _watchdog(self) -> None:
        """Periodically mark agents offline if they stop sending data."""
        while True:
            await asyncio.sleep(WATCHDOG_INTERVAL)
            now = time.time()
            async with self._lock:
                agent_ids = list(self._agents.keys())

            for agent_id in agent_ids:
                async with self._lock:
                    conn = self._agents.get(agent_id)
                if not conn or conn.ws is None:
                    continue
                elapsed = now - conn.last_seen
                if elapsed > AGENT_OFFLINE_TIMEOUT:
                    logger.warning(
                        "Agent %s timed out (%.0fs since last message). Marking offline.",
                        agent_id, elapsed,
                    )
                    await self.handle_agent_disconnect(agent_id)


# ── Singleton ──────────────────────────────────────────────────────────────────

manager = WebSocketManager()


# ── Utilities ──────────────────────────────────────────────────────────────────

def _slug(name: str) -> str:
    """Convert agent name to a stable lowercase slug used as agent_id."""
    import re
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "agent"
