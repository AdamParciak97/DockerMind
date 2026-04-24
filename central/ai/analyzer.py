"""
ai/analyzer.py — AI analysis engine for DockerMind.

Uses OpenAI-compatible client pointed at ai.mgmt.pl/llama3/v1.
TLS verification controlled by AI_VERIFY_SSL / AI_CA_CERT env vars.
Streams tokens to dashboards via WebSocket broadcast.
Parses risk level from response.
Saves completed Analysis to DB.
"""

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Callable, Awaitable

import httpx
from openai import OpenAI

from config import settings
from models import Analysis

logger = logging.getLogger(__name__)

# ── AI client (module-level singleton) ────────────────────────────────────────

_client: OpenAI | None = None

# Limit concurrent AI analyses to avoid overloading the AI server
_ANALYSIS_SEMAPHORE: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _ANALYSIS_SEMAPHORE
    if _ANALYSIS_SEMAPHORE is None:
        _ANALYSIS_SEMAPHORE = asyncio.Semaphore(3)
    return _ANALYSIS_SEMAPHORE


def get_client() -> OpenAI:
    global _client
    if _client is None:
        ssl_verify: bool | str = settings.AI_CA_CERT if settings.AI_CA_CERT else settings.AI_VERIFY_SSL
        if not ssl_verify:
            logger.warning("AI_VERIFY_SSL=false — TLS certificate verification is disabled")
        _client = OpenAI(
            base_url=settings.AI_BASE_URL,
            api_key="none",
            http_client=httpx.Client(
                verify=ssl_verify,
                timeout=httpx.Timeout(
                    connect=10.0,
                    read=settings.AI_TIMEOUT,
                    write=10.0,
                    pool=10.0,
                ),
            ),
        )
        logger.info("AI client initialised → %s", settings.AI_BASE_URL)
    return _client


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "Jesteś ekspertem DevOps i administratorem Linux. "
    "Analizujesz dane kontenera Docker. "
    "Odpowiadaj WYŁĄCZNIE po polsku. "
    "Bądź konkretny - podawaj gotowe komendy do skopiowania."
)

# ── Analysis prompt template ──────────────────────────────────────────────────

def build_prompt(snapshot: dict) -> str:
    name     = snapshot.get("name", "nieznany")
    image    = snapshot.get("image", "nieznany")
    status   = snapshot.get("status", "nieznany")
    uptime   = snapshot.get("uptime") or "brak danych"
    restarts = snapshot.get("restart_count", 0)
    exit_code= snapshot.get("exit_code", 0)
    last_crash = snapshot.get("last_crash") or "brak"
    cpu      = snapshot.get("cpu_percent", 0.0)
    mem      = snapshot.get("memory", {})
    mem_used = _fmt_bytes(mem.get("usage_bytes", 0))
    mem_lim  = _fmt_bytes(mem.get("limit_bytes", 0))
    mem_pct  = mem.get("percent", 0.0)
    logs     = snapshot.get("logs", "brak logów")
    compose  = snapshot.get("compose") or "Plik docker-compose.yml nie został znaleziony."

    # Trim logs to last 200 lines for prompt
    log_lines = logs.splitlines()
    if len(log_lines) > 200:
        log_lines = log_lines[-200:]
    logs_trimmed = "\n".join(log_lines)

    return f"""Przeanalizuj poniższy kontener Docker i dostarcz szczegółową diagnozę.

## Dane kontenera

- **Nazwa:** {name}
- **Obraz:** {image}
- **Status:** {status}
- **Uptime / czas zatrzymania:** {uptime}
- **Liczba restartów:** {restarts}
- **Ostatni kod wyjścia:** {exit_code}
- **Ostatnia awaria:** {last_crash}
- **CPU:** {cpu}%
- **RAM:** {mem_used} / {mem_lim} ({mem_pct}%)

## Ostatnie logi (do 200 linii)

```
{logs_trimmed}
```

## docker-compose.yml

```yaml
{compose}
```

---

Odpowiedz w następującej strukturze (użyj dokładnie tych nagłówków):

## 🔍 Diagnoza problemu
(co jest nie tak, co powoduje problem)

## ⚠️ Ocena ryzyka: [NISKI/ŚREDNI/WYSOKI/KRYTYCZNY]
(uzasadnienie oceny)

## 🔧 Zalecane naprawy
(konkretne kroki do naprawy)

## 💻 Komendy do wykonania
(gotowe komendy shell do skopiowania i uruchomienia)

## 🛡️ Rekomendacje prewencyjne
(jak zapobiec problemowi w przyszłości)
"""


# ── Main analysis function ─────────────────────────────────────────────────────

async def analyze_container(
    agent_id: str,
    snapshot: dict,
    broadcast_fn: Callable[[str, dict], Awaitable[None]],
) -> Analysis:
    """
    Stream AI analysis for a container snapshot.

    - Streams tokens to dashboards via broadcast_fn.
    - Returns a completed (unsaved) Analysis model.
    """
    container_name = snapshot.get("name", "unknown")
    container_image = snapshot.get("image", "")

    logger.info("Starting AI analysis: agent=%s container=%s", agent_id, container_name)

    # Notify dashboards: analysis starting
    await broadcast_fn("analysis_start", {
        "agent_id": agent_id,
        "container_name": container_name,
    })

    prompt = build_prompt(snapshot)
    client = get_client()

    full_response = ""
    risk_level = "NIEZNANY"

    async with _get_semaphore():
        try:
            # Run blocking OpenAI call in thread executor to avoid blocking event loop
            def _stream_sync():
                return client.chat.completions.create(
                    model=settings.AI_MODEL,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",   "content": prompt},
                    ],
                    stream=True,
                    temperature=0.3,
                    max_tokens=2048,
                )

            loop = asyncio.get_running_loop()
            stream = await loop.run_in_executor(None, _stream_sync)

            # Iterate stream synchronously in executor, broadcast each chunk
            async def _consume():
                nonlocal full_response, risk_level
                buffer = ""

                def _iter():
                    chunks = []
                    for chunk in stream:
                        delta = chunk.choices[0].delta.content or ""
                        chunks.append(delta)
                    return chunks

                chunks = await loop.run_in_executor(None, _iter)

                for delta in chunks:
                    if not delta:
                        continue
                    full_response += delta
                    buffer += delta

                    # Broadcast every token (or small batch) to dashboards
                    await broadcast_fn("analysis_token", {
                        "agent_id": agent_id,
                        "container_name": container_name,
                        "token": delta,
                    })

                    # Parse risk level as soon as we see it in the stream
                    if risk_level == "NIEZNANY":
                        risk_level = _extract_risk(full_response)

            await _consume()

        except Exception as e:
            logger.error("AI streaming error for %s/%s: %s", agent_id, container_name, e)
            error_msg = "\n\n❌ Błąd analizy AI. Sprawdź logi serwera."
            full_response += error_msg
            await broadcast_fn("analysis_token", {
                "agent_id": agent_id,
                "container_name": container_name,
                "token": error_msg,
            })

    # Final risk parse on complete response
    if risk_level == "NIEZNANY":
        risk_level = _extract_risk(full_response)

    logger.info(
        "Analysis complete: agent=%s container=%s risk=%s chars=%d",
        agent_id, container_name, risk_level, len(full_response),
    )

    return Analysis(
        agent_id=agent_id,
        container_name=container_name,
        container_image=container_image,
        risk_level=risk_level,
        content=full_response,
        cpu_percent=snapshot.get("cpu_percent", 0.0),
        mem_percent=snapshot.get("memory", {}).get("percent", 0.0),
        restart_count=snapshot.get("restart_count", 0),
        exit_code=snapshot.get("exit_code", 0),
        last_crash=snapshot.get("last_crash"),
        created_at=datetime.now(timezone.utc),
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

_RISK_PATTERN = re.compile(
    r"Ocena ryzyka[^:]*:\s*\[?(NISKI|ŚREDNI|WYSOKI|KRYTYCZNY)\]?",
    re.IGNORECASE,
)


def _extract_risk(text: str) -> str:
    """Parse risk level from AI response text."""
    match = _RISK_PATTERN.search(text)
    if match:
        return match.group(1).upper()
    # Fallback: look for bare keywords near risk section
    for level in ("KRYTYCZNY", "WYSOKI", "ŚREDNI", "NISKI"):
        if level in text.upper():
            return level
    return "NIEZNANY"


def _fmt_bytes(n: int) -> str:
    """Format byte count as human-readable string."""
    if n == 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"
