"""
main.py — DockerMind Agent
WebSocket client: connects to central server, streams Docker data every 30s,
handles on-demand requests, auto-reconnects with exponential backoff.
"""

import asyncio
import base64
import fcntl
import json
import logging
import os
import pty
import re
import socket
import struct
import sys
import termios
import time
from typing import Optional

_CONTAINER_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.\-]*$')
_ALLOWED_ACTIONS = {"start", "stop", "restart"}
_MAX_EXEC_SESSIONS = 10

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
CENTRAL_URL: str  = os.getenv("CENTRAL_URL", "ws://localhost/ws/agent")
AGENT_TOKEN: str  = os.getenv("AGENT_TOKEN", "")
AGENT_NAME: str   = os.getenv("AGENT_NAME", socket.gethostname())
AGENT_IP: str     = os.getenv("AGENT_IP", "")   # explicit host IP (recommended)

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


def get_host_hostname() -> str:
    """Read the host machine's hostname from the mounted host filesystem."""
    host_root = os.getenv("HOST_ROOT", "/host")
    for path in [
        os.path.join(host_root, "etc", "hostname"),
        "/etc/hostname",
    ]:
        try:
            with open(path) as f:
                h = f.read().strip()
                if h:
                    return h
        except Exception:
            pass
    return socket.gethostname()


def get_host_ip() -> str:
    """
    Detect host's primary IP via /proc/1/net (host network namespace).
    Mounted as /host-proc-net in docker-compose. Falls back to container IP.
    """
    import re
    try:
        # Step 1: find default-route interface from routing table
        default_iface = None
        with open("/host-proc-net/route") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "00000000":
                    default_iface = parts[0]
                    break

        # Step 2: parse fib_trie to collect all LOCAL IPs
        with open("/host-proc-net/fib_trie") as f:
            content = f.read()

        # Each LOCAL IP appears as:   |-- A.B.C.D\n        /32 host LOCAL
        local_ips = re.findall(
            r'\|--\s+([\d.]+)\n\s+/32 host LOCAL', content
        )

        # Step 3: score candidates — prefer IPs on the default interface subnet
        # Exclude loopback and all-zeros
        candidates = [
            ip for ip in local_ips
            if not ip.startswith("127.") and ip != "0.0.0.0"
        ]

        if candidates:
            # Prefer non-Docker-bridge IPs (172.17/18/19/20)
            non_docker = [
                ip for ip in candidates
                if not re.match(r'^172\.(1[7-9]|2[0-9]|3[01])\\.', ip)
            ]
            return (non_docker or candidates)[0]
    except Exception:
        pass
    return get_local_ip()


def build_registration() -> dict:
    docker_info = collector.get_docker_info()
    return {
        "type": "register",
        "agent_name": AGENT_NAME,
        "hostname": get_host_hostname(),
        "ip": AGENT_IP or get_host_ip(),
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


# ── Exec sessions (browser terminal) ─────────────────────────────────────────

_exec_sessions: dict[str, dict] = {}  # session_id → {master_fd, proc, task}


def _set_pty_size(fd: int, cols: int, rows: int) -> None:
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except Exception:
        pass


async def _read_pty_output(session_id: str, master_fd: int, ws) -> None:
    """Read from PTY master and forward to central as base64."""
    loop = asyncio.get_running_loop()
    try:
        while True:
            try:
                data = await loop.run_in_executor(None, os.read, master_fd, 4096)
            except OSError:
                break
            if not data:
                break
            await send_json(ws, {
                "type": "exec_output",
                "session_id": session_id,
                "data": base64.b64encode(data).decode(),
            })
    except asyncio.CancelledError:
        pass
    finally:
        # Notify central the session ended
        try:
            await send_json(ws, {"type": "exec_ended", "session_id": session_id})
        except Exception:
            pass
        _exec_sessions.pop(session_id, None)


async def handle_exec_start(ws, session_id: str, container: str, cols: int, rows: int) -> None:
    if not _CONTAINER_RE.match(container):
        logger.warning("exec_start rejected — invalid container name: %r", container[:64])
        await send_json(ws, {"type": "exec_ended", "session_id": session_id})
        return
    if len(_exec_sessions) >= _MAX_EXEC_SESSIONS:
        logger.warning("exec_start rejected — session limit (%d) reached", _MAX_EXEC_SESSIONS)
        await send_json(ws, {"type": "exec_ended", "session_id": session_id})
        return
    master_fd, slave_fd = pty.openpty()
    _set_pty_size(slave_fd, cols, rows)

    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", "-it", "--", container, "/bin/sh",
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            preexec_fn=os.setsid,
            close_fds=True,
        )
    except Exception as e:
        os.close(slave_fd)
        os.close(master_fd)
        logger.error("exec_start failed for %s: %s", container, e)
        await send_json(ws, {"type": "exec_ended", "session_id": session_id})
        return

    os.close(slave_fd)
    task = asyncio.create_task(_read_pty_output(session_id, master_fd, ws))
    _exec_sessions[session_id] = {"master_fd": master_fd, "proc": proc, "task": task}
    logger.info("Exec session started: %s → %s", session_id[:8], container)


async def handle_exec_input(session_id: str, data_b64: str) -> None:
    sess = _exec_sessions.get(session_id)
    if not sess:
        return
    try:
        data = base64.b64decode(data_b64)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, os.write, sess["master_fd"], data)
    except OSError:
        pass


def handle_exec_resize(session_id: str, cols: int, rows: int) -> None:
    sess = _exec_sessions.get(session_id)
    if sess:
        _set_pty_size(sess["master_fd"], cols, rows)


async def handle_exec_end(session_id: str) -> None:
    sess = _exec_sessions.pop(session_id, None)
    if not sess:
        return
    sess["task"].cancel()
    try:
        sess["proc"].terminate()
    except Exception:
        pass
    try:
        os.close(sess["master_fd"])
    except Exception:
        pass
    logger.info("Exec session ended: %s", session_id[:8])


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
                logger.error("trigger_analysis collect failed for %s: %s", container_name, e)
                data = {"error": "Błąd zbierania danych kontenera."}
            await send_json(ws, {
                "type": "response",
                "request_id": request_id,
                "action": action,
                "data": data,
            })

        elif action == "container_action":
            container = params.get("container", "")
            act = params.get("action", "")
            if not _CONTAINER_RE.match(container):
                raise ValueError("Nieprawidłowa nazwa kontenera.")
            if act not in _ALLOWED_ACTIONS:
                raise ValueError(f"Niedozwolona akcja: {act!r}")
            result = collector.container_action(container, act)
            await send_json(ws, {
                "type": "response",
                "request_id": request_id,
                "action": action,
                "data": result,
            })

        elif action == "save_compose":
            container = params.get("container", "")
            content = params.get("content", "")
            if not _CONTAINER_RE.match(container):
                raise ValueError("Nieprawidłowa nazwa kontenera.")
            result = collector.save_compose_file(container, content)
            await send_json(ws, {
                "type": "response",
                "request_id": request_id,
                "action": action,
                "data": result,
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

    except (ValueError, TypeError) as e:
        logger.warning("Invalid request %s: %s", action, e)
        await send_json(ws, {
            "type": "error",
            "request_id": request_id,
            "message": str(e),
        })
    except Exception as e:
        logger.error("Error handling request %s: %s", action, e)
        await send_json(ws, {
            "type": "error",
            "request_id": request_id,
            "message": "Wewnętrzny błąd agenta.",
        })


# ── Main connection loop ───────────────────────────────────────────────────────

async def stream_loop(ws) -> None:
    """Periodically collect and send full data payload."""
    while True:
        try:
            payload = await asyncio.get_running_loop().run_in_executor(
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
            elif msg_type == "exec_start":
                asyncio.create_task(handle_exec_start(
                    ws,
                    message.get("session_id", ""),
                    message.get("container", ""),
                    int(message.get("cols", 80)),
                    int(message.get("rows", 24)),
                ))
            elif msg_type == "exec_input":
                asyncio.create_task(handle_exec_input(
                    message.get("session_id", ""),
                    message.get("data", ""),
                ))
            elif msg_type == "exec_resize":
                handle_exec_resize(
                    message.get("session_id", ""),
                    int(message.get("cols", 80)),
                    int(message.get("rows", 24)),
                )
            elif msg_type == "exec_end":
                asyncio.create_task(handle_exec_end(message.get("session_id", "")))
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
        reg = await asyncio.get_running_loop().run_in_executor(
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
        logger.critical("AGENT_TOKEN is not set. Edit .env and restart.")
        sys.exit(1)

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
