"""
main.py — DockerMind Agent
WebSocket client: connects to central server, streams Docker data every 30s,
handles on-demand requests, auto-reconnects with exponential backoff.
"""

import asyncio
import json
import logging
import os
import socket
import time
from typing import Optional

import websockets
from dotenv import load_dotenv

import collector

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
CENTRAL_URL: str = os.getenv("CENTRAL_URL", "ws://localhost/ws/agent")
AGENT_TOKEN: str = os.getenv("AGENT_TOKEN", "")
AGENT_NAME: str = os.getenv("AGENT_NAME", socket.gethostname())

STREAM_INTERVAL = 30          # seconds between full data pushes
PING_INTERVAL   = 20          # WebSocket ping keepalive
PING_TIMEOUT    = 60          # seconds before ping timeout = disconnect

BACKOFF_MIN  = 1
BACKOFF_MAX  = 60
BACKOFF_MULT = 2


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def build_registration() -> dict:
    docker_info = collector.get_docker_info()
    return {
        "type": "register",
        "agent_name": AGENT_NAME,
        "hostname": socket.gethostname(),
        "ip": get_local_ip(),
        "docker_version": docker_info.get("docker_version", "unknown"),
        "os": docker_info.get("os", "unknown"),
        "kernel": docker_info.get("kernel", "unknown"),
        "cpus": docker_info.get("cpus", 0),
        "total_memory": docker_info.get("total_memory", 0),
    }


def build_data_payload() -> dict:
    containers = collector.collect_all_containers(with_stats=True)
    return {
        "type": "data",
        "agent_name": AGENT_NAME,
        "timestamp": time.time(),
        "containers": containers,
    }


async def send_json(ws, payload: dict) -> None:
    await ws.send(json.dumps(payload, ensure_ascii=False))


# ── On-demand request handler ─────────────────────────────────────────────────

async def handle_request(ws, message: dict) -> None:
    action = message.get("action")
    request_id = message.get("request_id")
    params = message.get("params", {})

    logger.info("On-demand request: action=%s params=%s", action, params)

    try:
        if action == "get_logs":
            container = params.get("container", "")
            lines = int(params.get("lines", 200))
            result = collector.get_container_logs(container, lines)
            await send_json(ws, {
                "type": "response",
                "request_id": request_id,
                "action": action,
                "data": result,
            })

        elif action == "get_compose":
            container = params.get("container", "")
            result = collector.get_container_compose(container)
            await send_json(ws, {
                "type": "response",
                "request_id": request_id,
                "action": action,
                "data": result,
            })

        elif action == "trigger_analysis":
            # Collect full single-container snapshot for AI analysis
            container_name = params.get("container", "")
            try:
                client = collector.get_docker_client()
                c = client.containers.get(container_name)
                data = collector.collect_container_data(c, with_stats=True)
            except Exception as e:
                data = {"error": str(e)}
            await send_json(ws, {
                "type": "response",
                "request_id": request_id,
                "action": action,
                "data": data,
            })

        elif action == "ping":
            await send_json(ws, {
                "type": "response",
                "request_id": request_id,
                "action": "pong",
                "data": "ok",
            })

        else:
            await send_json(ws, {
                "type": "error",
                "request_id": request_id,
                "message": f"Nieznana akcja: {action}",
            })

    except Exception as e:
        logger.error("Error handling request %s: %s", action, e)
        await send_json(ws, {
            "type": "error",
            "request_id": request_id,
            "message": str(e),
        })


# ── Main connection loop ───────────────────────────────────────────────────────

async def stream_loop(ws) -> None:
    """Periodically collect and send full data payload."""
    while True:
        try:
            payload = await asyncio.get_event_loop().run_in_executor(
                None, build_data_payload
            )
            await send_json(ws, payload)
            logger.info(
                "Streamed %d containers to central.", len(payload["containers"])
            )
        except Exception as e:
            logger.error("Error building/sending data payload: %s", e)
            raise  # bubble up → triggers reconnect
        await asyncio.sleep(STREAM_INTERVAL)


async def receive_loop(ws) -> None:
    """Listen for on-demand requests from central server."""
    async for raw in ws:
        try:
            message = json.loads(raw)
            msg_type = message.get("type")
            if msg_type == "request":
                asyncio.create_task(handle_request(ws, message))
            elif msg_type == "ack":
                logger.debug("ACK from central: %s", message.get("message", ""))
            else:
                logger.debug("Unhandled message type: %s", msg_type)
        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON from central: %s", e)


async def connect_and_run() -> None:
    """Open WebSocket, register, then run stream + receive loops concurrently."""
    # Pass token as query param — compatible with all websockets versions
    sep = "&" if "?" in CENTRAL_URL else "?"
    url = f"{CENTRAL_URL}{sep}agent_token={AGENT_TOKEN}" if AGENT_TOKEN else CENTRAL_URL

    logger.info("Connecting to %s ...", CENTRAL_URL)
    async with websockets.connect(
        url,
        ping_interval=PING_INTERVAL,
        ping_timeout=PING_TIMEOUT,
        max_size=64 * 1024 * 1024,   # 64 MB — logs can be large
        open_timeout=30,
    ) as ws:
        logger.info("Connected. Sending registration...")
        reg = await asyncio.get_event_loop().run_in_executor(
            None, build_registration
        )
        await send_json(ws, reg)
        logger.info("Registered as '%s' (%s)", AGENT_NAME, reg["ip"])

        # Run both loops concurrently; if either raises → reconnect
        stream_task  = asyncio.create_task(stream_loop(ws))
        receive_task = asyncio.create_task(receive_loop(ws))

        done, pending = await asyncio.wait(
            [stream_task, receive_task],
            return_when=asyncio.FIRST_EXCEPTION,
        )
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc:
                raise exc


async def main() -> None:
    if not AGENT_TOKEN:
        logger.error("AGENT_TOKEN is not set. Edit .env and restart.")
        return

    backoff = BACKOFF_MIN
    while True:
        try:
            await connect_and_run()
            backoff = BACKOFF_MIN  # reset on clean disconnect
        except (
            websockets.exceptions.ConnectionClosed,
            websockets.exceptions.WebSocketException,
            OSError,
            ConnectionRefusedError,
        ) as e:
            logger.warning("Connection lost: %s", e)
        except Exception as e:
            logger.error("Unexpected error: %s", e)

        logger.info("Reconnecting in %ds...", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * BACKOFF_MULT, BACKOFF_MAX)


if __name__ == "__main__":
    asyncio.run(main())
