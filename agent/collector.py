"""
collector.py — Docker SDK data collection for DockerMind agent.
Collects container list, stats, logs, compose files, crash info.
"""

import os
import glob
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import docker
from docker.errors import DockerException, NotFound, APIError

logger = logging.getLogger(__name__)

# Directories to search for docker-compose files
COMPOSE_SEARCH_DIRS = [
    "/etc/dockermind",
    "/opt",
    "/home",
    "/srv",
    "/root",
    "/var/lib/docker/compose",
]

COMPOSE_FILENAMES = ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]

# Host filesystem root as seen from inside the container.
# When running in Docker: HOST_ROOT=/host (/ of host mounted at /host).
# When running directly on host: HOST_ROOT="" (empty).
HOST_ROOT = os.getenv("HOST_ROOT", "/host")

_CONTAINER_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.\-]*$')


def _hp(path: str) -> str:
    """Translate a host absolute path to its location inside the container.
    Validates that the resolved path stays within HOST_ROOT to prevent traversal."""
    if not HOST_ROOT:
        return path
    if path.startswith(HOST_ROOT):
        real = os.path.realpath(path)
    else:
        real = os.path.realpath(os.path.join(HOST_ROOT, path.lstrip("/")))
    allowed = os.path.realpath(HOST_ROOT)
    if not real.startswith(allowed + os.sep) and real != allowed:
        logger.warning("Path traversal blocked: %r resolves outside HOST_ROOT", path)
        return ""
    return real


def get_docker_client() -> docker.DockerClient:
    return docker.from_env()


def get_docker_info() -> dict:
    """Return Docker daemon version and OS info."""
    try:
        client = get_docker_client()
        info = client.info()
        version = client.version()
        return {
            "docker_version": version.get("Version", "unknown"),
            "os": info.get("OperatingSystem", "unknown"),
            "kernel": info.get("KernelVersion", "unknown"),
            "architecture": info.get("Architecture", "unknown"),
            "total_memory": info.get("MemTotal", 0),
            "cpus": info.get("NCPU", 0),
        }
    except DockerException as e:
        logger.error("Failed to get Docker info: %s", e)
        return {}


def _parse_cpu_percent(stats: dict) -> float:
    """Calculate CPU usage percentage from raw Docker stats."""
    try:
        cpu_delta = (
            stats["cpu_stats"]["cpu_usage"]["total_usage"]
            - stats["precpu_stats"]["cpu_usage"]["total_usage"]
        )
        system_delta = (
            stats["cpu_stats"]["system_cpu_usage"]
            - stats["precpu_stats"]["system_cpu_usage"]
        )
        num_cpus = stats["cpu_stats"].get("online_cpus") or len(
            stats["cpu_stats"]["cpu_usage"].get("percpu_usage", [1])
        )
        if system_delta > 0 and cpu_delta >= 0:
            return round((cpu_delta / system_delta) * num_cpus * 100.0, 2)
    except (KeyError, ZeroDivisionError, TypeError):
        pass
    return 0.0


def _parse_network(stats: dict) -> dict:
    """Extract cumulative network RX/TX bytes from raw Docker stats."""
    try:
        networks = stats.get("networks", {})
        rx = sum(n.get("rx_bytes", 0) for n in networks.values())
        tx = sum(n.get("tx_bytes", 0) for n in networks.values())
        return {"rx_bytes": rx, "tx_bytes": tx}
    except (KeyError, TypeError):
        return {"rx_bytes": 0, "tx_bytes": 0}


def _parse_blkio(stats: dict) -> dict:
    """Extract block I/O read/write bytes from raw Docker stats."""
    try:
        entries = stats.get("blkio_stats", {}).get("io_service_bytes_recursive") or []
        read_b  = sum(e.get("value", 0) for e in entries if e.get("op", "").lower() == "read")
        write_b = sum(e.get("value", 0) for e in entries if e.get("op", "").lower() == "write")
        return {"read_bytes": read_b, "write_bytes": write_b}
    except (KeyError, TypeError):
        return {"read_bytes": 0, "write_bytes": 0}


def _parse_memory(stats: dict) -> dict:
    """Extract memory usage and limit from raw Docker stats."""
    try:
        mem = stats["memory_stats"]
        usage = mem.get("usage", 0)
        # Subtract cache from usage (Linux kernel reports cache inside usage)
        cache = mem.get("stats", {}).get("cache", 0)
        real_usage = max(usage - cache, 0)
        limit = mem.get("limit", 0)
        return {
            "usage_bytes": real_usage,
            "limit_bytes": limit,
            "percent": round((real_usage / limit * 100) if limit > 0 else 0.0, 2),
        }
    except (KeyError, TypeError):
        return {"usage_bytes": 0, "limit_bytes": 0, "percent": 0.0}


def _get_container_uptime(container) -> Optional[str]:
    """Return human-readable uptime or stopped-since string."""
    try:
        state = container.attrs.get("State", {})
        status = state.get("Status", "")
        if status == "running":
            started = state.get("StartedAt", "")
            if started and started != "0001-01-01T00:00:00Z":
                start_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                delta = datetime.now(timezone.utc) - start_dt
                seconds = int(delta.total_seconds())
                if seconds < 60:
                    return f"{seconds}s"
                elif seconds < 3600:
                    return f"{seconds // 60}m {seconds % 60}s"
                elif seconds < 86400:
                    h = seconds // 3600
                    m = (seconds % 3600) // 60
                    return f"{h}h {m}m"
                else:
                    d = seconds // 86400
                    h = (seconds % 86400) // 3600
                    return f"{d}d {h}h"
        elif status in ("exited", "dead"):
            finished = state.get("FinishedAt", "")
            if finished and finished != "0001-01-01T00:00:00Z":
                return f"zatrzymany od {finished[:19].replace('T', ' ')}"
    except Exception:
        pass
    return None


def _read_file(path: str) -> Optional[str]:
    """Read file, return content or None on error."""
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def _find_compose_file(container) -> Optional[str]:
    """
    Try to find a docker-compose file related to the given container.
    Search order:
      1. com.docker.compose.project.config_files label (exact path set by docker compose)
      2. com.docker.compose.project.working_dir label + compose filenames
      3. /etc/dockermind/<container-name>/
      4. Broad recursive search in common dirs
    Returns file content as string, or None if not found.
    """
    labels = container.labels or {}
    name   = container.name
    project = labels.get("com.docker.compose.project", "")

    logger.debug(
        "compose search [%s] config_files=%r working_dir=%r project=%r",
        name,
        labels.get("com.docker.compose.project.config_files", ""),
        labels.get("com.docker.compose.project.working_dir", ""),
        project,
    )

    # 1. Direct path from label — docker compose always sets this
    #    Value can be comma-separated list of files (override files)
    config_files = labels.get("com.docker.compose.project.config_files", "")
    if config_files:
        for path in config_files.split(","):
            path = path.strip()
            if path and os.path.splitext(path)[1].lower() in (".yml", ".yaml"):
                translated = _hp(path)
                if translated:
                    content = _read_file(translated)
                    if content:
                        return content

    # 2. Working dir label — search for compose filename inside it
    working_dir = labels.get("com.docker.compose.project.working_dir", "")
    if working_dir:
        translated_dir = _hp(working_dir)
        if translated_dir:
            for fname in COMPOSE_FILENAMES:
                content = _read_file(os.path.join(translated_dir, fname))
                if content:
                    return content

    # 3. /etc/dockermind/<container-name>/  (always on host, mounted directly)
    if _CONTAINER_RE.match(name):
        for fname in COMPOSE_FILENAMES:
            content = _read_file(f"/etc/dockermind/{name}/{fname}")
            if content:
                return content

    # 4. Broad recursive search under HOST_ROOT — match by project name or container name
    search_dirs = [os.path.join(HOST_ROOT, d.lstrip("/")) for d in COMPOSE_SEARCH_DIRS] \
                  if HOST_ROOT else COMPOSE_SEARCH_DIRS
    for base_dir in search_dirs:
        if not os.path.isdir(base_dir):
            continue
        for fname in COMPOSE_FILENAMES:
            matches = glob.glob(os.path.join(base_dir, "**", fname), recursive=True)
            for match in matches:
                match_lower = match.lower()
                if name in match_lower or (project and project in match_lower):
                    content = _read_file(match)
                    if content:
                        return content

    return None


def collect_container_data(container, with_stats: bool = True) -> dict:
    """Collect all data for a single container."""
    container.reload()
    state = container.attrs.get("State", {})
    status = state.get("Status", "unknown")
    exit_code = state.get("ExitCode", 0)
    restart_count = container.attrs.get("RestartCount", 0)

    # Last crash timestamp
    last_crash = None
    if exit_code != 0 or restart_count > 0:
        finished = state.get("FinishedAt", "")
        if finished and finished != "0001-01-01T00:00:00Z":
            last_crash = finished[:19].replace("T", " ") + " UTC"

    # Stats (CPU + RAM + network + blkio + pids)
    cpu_percent = 0.0
    memory  = {"usage_bytes": 0, "limit_bytes": 0, "percent": 0.0}
    network = {"rx_bytes": 0, "tx_bytes": 0}
    blkio   = {"read_bytes": 0, "write_bytes": 0}
    pids    = 0
    if with_stats and status == "running":
        try:
            raw_stats   = container.stats(stream=False)
            cpu_percent = _parse_cpu_percent(raw_stats)
            memory      = _parse_memory(raw_stats)
            network     = _parse_network(raw_stats)
            blkio       = _parse_blkio(raw_stats)
            pids        = raw_stats.get("pids_stats", {}).get("current", 0) or 0
        except (APIError, Exception) as e:
            logger.warning("Stats error for %s: %s", container.name, e)

    # Logs — last 100 lines with timestamps
    logs = ""
    try:
        logs = container.logs(
            tail=100,
            timestamps=True,
            stdout=True,
            stderr=True,
        ).decode("utf-8", errors="replace")
    except (APIError, Exception) as e:
        logger.warning("Logs error for %s: %s", container.name, e)

    # docker-compose.yml
    compose_content = _find_compose_file(container)

    return {
        "name": container.name,
        "id": container.short_id,
        "image": container.image.tags[0] if container.image.tags else container.attrs["Config"]["Image"],
        "status": status,
        "uptime": _get_container_uptime(container),
        "restart_count": restart_count,
        "exit_code": exit_code,
        "last_crash": last_crash,
        "cpu_percent": cpu_percent,
        "memory": memory,
        "network": network,
        "blkio": blkio,
        "pids": pids,
        "logs": logs,
        "compose": compose_content,
        "labels": container.labels,
    }


def collect_all_containers(with_stats: bool = True) -> list[dict]:
    """Collect data for all containers (running + stopped)."""
    try:
        client = get_docker_client()
        containers = client.containers.list(all=True)
        result = []
        for c in containers:
            try:
                data = collect_container_data(c, with_stats=with_stats)
                result.append(data)
            except Exception as e:
                logger.error("Error collecting data for container %s: %s", c.name, e)
        return result
    except DockerException as e:
        logger.error("Cannot connect to Docker daemon: %s", e)
        return []


def get_container_logs(container_name: str, lines: int = 200) -> str:
    """Fetch last N log lines for a specific container."""
    try:
        client = get_docker_client()
        container = client.containers.get(container_name)
        return container.logs(
            tail=lines,
            timestamps=True,
            stdout=True,
            stderr=True,
        ).decode("utf-8", errors="replace")
    except NotFound:
        return f"Kontener '{container_name}' nie znaleziony."
    except (APIError, Exception) as e:
        return f"Błąd pobierania logów: {e}"


def get_container_compose(container_name: str) -> str:
    """Return docker-compose content for a specific container."""
    try:
        client = get_docker_client()
        container = client.containers.get(container_name)
        content = _find_compose_file(container)
        return content if content else "Plik docker-compose.yml nie został znaleziony."
    except NotFound:
        return f"Kontener '{container_name}' nie znaleziony."
    except Exception as e:
        return f"Błąd: {e}"


def container_action(container_name: str, action: str) -> dict:
    """Perform start / stop / restart on a container."""
    try:
        client = get_docker_client()
        c = client.containers.get(container_name)
        if action == "start":
            c.start()
        elif action == "stop":
            c.stop(timeout=10)
        elif action == "restart":
            c.restart(timeout=10)
        else:
            return {"success": False, "error": f"Nieznana akcja: {action}"}
        c.reload()
        return {"success": True, "action": action, "status": c.status}
    except NotFound:
        return {"success": False, "error": f"Kontener '{container_name}' nie znaleziony."}
    except (APIError, Exception) as e:
        return {"success": False, "error": str(e)}


def _find_compose_path(container) -> Optional[str]:
    """Return the host-absolute path of the compose file for this container, or None."""
    labels = container.labels or {}

    config_files = labels.get("com.docker.compose.project.config_files", "")
    if config_files:
        path = config_files.split(",")[0].strip()
        if path:
            return path  # real host path (not translated)

    working_dir = labels.get("com.docker.compose.project.working_dir", "")
    if working_dir:
        for fname in COMPOSE_FILENAMES:
            candidate = os.path.join(working_dir, fname)
            if os.path.exists(_hp(candidate)):
                return candidate

    name = container.name
    if _CONTAINER_RE.match(name):
        for fname in COMPOSE_FILENAMES:
            candidate = f"/etc/dockermind/{name}/{fname}"
            if os.path.exists(candidate):
                return candidate

    return None


_COMPOSE_WRITE_ALLOWLIST = [
    "/etc/dockermind",
    "/opt",
    "/home",
    "/srv",
    "/root",
    "/var/lib/docker/compose",
]


def save_compose_file(container_name: str, content: str) -> dict:
    """Write docker-compose content back to the host filesystem."""
    try:
        client = get_docker_client()
        container = client.containers.get(container_name)
        host_path = _find_compose_path(container)
        if not host_path:
            return {"success": False, "error": "Nie można określić ścieżki do pliku compose."}

        # Validate the extension
        if os.path.splitext(host_path)[1].lower() not in (".yml", ".yaml"):
            return {"success": False, "error": "Dozwolony zapis tylko do plików .yml/.yaml."}

        write_path = _hp(host_path)
        if not write_path:
            return {"success": False, "error": "Ścieżka poza dozwolonym obszarem."}

        # Check path is inside an allowed base directory (host-side)
        real_host = os.path.realpath(host_path)
        allowed = [
            os.path.join(os.path.realpath(HOST_ROOT), d.lstrip("/"))
            for d in _COMPOSE_WRITE_ALLOWLIST
            if HOST_ROOT
        ] + ["/etc/dockermind"]
        if not any(real_host.startswith(a + os.sep) or real_host == a for a in allowed):
            logger.warning("save_compose blocked — path outside allowlist: %r", host_path)
            return {"success": False, "error": "Ścieżka poza dozwolonym obszarem."}

        with open(write_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"success": True, "path": host_path}
    except NotFound:
        return {"success": False, "error": "Kontener nie znaleziony."}
    except OSError as e:
        logger.error("save_compose OSError for %s: %s", container_name, e)
        return {"success": False, "error": "Błąd zapisu pliku."}
    except Exception as e:
        logger.error("save_compose error for %s: %s", container_name, e)
        return {"success": False, "error": "Wewnętrzny błąd agenta."}
