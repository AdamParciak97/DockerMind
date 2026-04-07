"""
routers/analysis.py — AI analysis endpoints.

POST   /api/analyze          trigger analysis (streams via WebSocket, saves to DB)
GET    /api/analyses          list saved analyses
GET    /api/analyses/{id}     single analysis
DELETE /api/analyses/{id}     delete analysis
GET    /api/servers/{agent_id}/containers/{name}/history   chart data
"""

import asyncio
import html as html_mod
import logging
import re
import smtplib
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
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


# ── PDF export ────────────────────────────────────────────────────────────────

@router.get("/api/analyses/{analysis_id}/pdf")
async def download_pdf(
    analysis_id: int,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
    obj = get_analysis(session, analysis_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Analiza nie znaleziona.")
    try:
        pdf_bytes = await asyncio.get_event_loop().run_in_executor(None, _generate_pdf, obj)
    except Exception as e:
        logger.error("PDF generation failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Błąd generowania PDF: {e}")
    filename = f"dockermind-{obj.container_name}-{obj.id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Email ─────────────────────────────────────────────────────────────────────

class EmailRequest(BaseModel):
    to: str


@router.post("/api/analyses/{analysis_id}/email")
async def email_analysis(
    analysis_id: int,
    body: EmailRequest,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
):
    from config import settings
    if not settings.SMTP_HOST:
        raise HTTPException(status_code=503, detail="SMTP nie jest skonfigurowany (brak SMTP_HOST w .env).")
    obj = get_analysis(session, analysis_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Analiza nie znaleziona.")
    try:
        await asyncio.get_event_loop().run_in_executor(None, _send_email, body.to, obj, settings)
    except Exception as e:
        logger.error("Email send failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Błąd wysyłki email: {e}")
    return {"sent": True, "to": body.to}


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


# ── PDF / Email internal helpers ───────────────────────────────────────────────

_RISK_COLORS_RGB = {
    "NISKI":     (22, 163, 74),
    "ŚREDNI":    (202, 138, 4),
    "WYSOKI":    (234, 88, 12),
    "KRYTYCZNY": (220, 38, 38),
}

_FONT_DIR = "/app/static/fonts"


def _generate_pdf(analysis) -> bytes:
    """Render an Analysis record as a PDF and return raw bytes."""
    from fpdf import FPDF

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(15, 15, 15)
    pdf.add_page()

    pdf.add_font("DejaVu",  "",  f"{_FONT_DIR}/DejaVuSans.ttf")
    pdf.add_font("DejaVu",  "B", f"{_FONT_DIR}/DejaVuSans-Bold.ttf")

    # ── Title ────────────────────────────────────────────────────────────────
    pdf.set_font("DejaVu", "B", 18)
    pdf.set_text_color(30, 58, 138)
    pdf.cell(0, 12, "DockerMind \u2014 Raport AI", new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(229, 231, 235)
    pdf.line(15, pdf.get_y(), 195, pdf.get_y())
    pdf.ln(4)

    # ── Metadata ─────────────────────────────────────────────────────────────
    r, g, b = _RISK_COLORS_RGB.get(analysis.risk_level, (107, 114, 128))
    meta = [
        ("Kontener:",     analysis.container_name),
        ("Obraz:",        analysis.container_image or "\u2014"),
        ("Ryzyko:",       analysis.risk_level),
        ("Data analizy:", analysis.created_at.strftime("%Y-%m-%d %H:%M")),
        ("CPU:",          f"{analysis.cpu_percent or 0}%"),
        ("RAM:",          f"{analysis.mem_percent or 0}%"),
    ]
    for label, value in meta:
        pdf.set_font("DejaVu", "B", 9)
        pdf.set_fill_color(249, 250, 251)
        pdf.set_text_color(55, 65, 81)
        pdf.cell(38, 7, label, fill=True)
        pdf.set_font("DejaVu", "", 9)
        if label == "Ryzyko:":
            pdf.set_text_color(r, g, b)
        else:
            pdf.set_text_color(31, 41, 55)
        pdf.cell(0, 7, value, new_x="LMARGIN", new_y="NEXT")

    pdf.ln(4)
    pdf.line(15, pdf.get_y(), 195, pdf.get_y())
    pdf.ln(5)

    # ── Content ───────────────────────────────────────────────────────────────
    content = analysis.content or ""
    # Strip code-fence markers, keep code body
    content = re.sub(r"```[^\n]*\n", "\n", content)
    content = re.sub(r"```", "\n", content)
    # Remove inline markdown formatting
    content = re.sub(r"\*\*([^*]+)\*\*", r"\1", content)
    content = re.sub(r"`([^`]+)`", r"[\1]", content)

    for line in content.split("\n"):
        h2 = re.match(r"^##\s+(.+)$", line)
        if h2:
            pdf.ln(3)
            pdf.set_font("DejaVu", "B", 11)
            pdf.set_text_color(67, 56, 202)
            pdf.cell(0, 8, h2.group(1), new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(31, 41, 55)
        elif line.strip():
            pdf.set_font("DejaVu", "", 9)
            pdf.set_text_color(31, 41, 55)
            pdf.multi_cell(0, 5, line)
        else:
            pdf.ln(2)

    # ── Footer ────────────────────────────────────────────────────────────────
    pdf.set_y(-18)
    pdf.set_font("DejaVu", "", 8)
    pdf.set_text_color(156, 163, 175)
    pdf.cell(0, 5, f"Wygenerowane przez DockerMind \u2014 {analysis.created_at.strftime('%Y-%m-%d %H:%M')}", align="C")

    return bytes(pdf.output())


def _analysis_to_html(analysis) -> str:
    """Convert analysis markdown content to an HTML email body."""
    r, g, b = _RISK_COLORS_RGB.get(analysis.risk_level, (107, 114, 128))
    risk_hex = f"#{r:02x}{g:02x}{b:02x}"

    content = html_mod.escape(analysis.content or "")
    content = re.sub(r"```[^\n]*\n(.*?)```",
                     lambda m: f'<pre style="background:#f3f4f6;padding:10px;border-radius:5px;font-size:12px">'
                               f'{m.group(1)}</pre>',
                     content, flags=re.DOTALL)
    content = re.sub(r"^## (.+)$",
                     r'<h3 style="color:#1d4ed8;border-bottom:1px solid #e5e7eb;padding-bottom:4px">\1</h3>',
                     content, flags=re.MULTILINE)
    content = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", content)
    content = re.sub(r"`([^`]+)`",
                     r'<code style="background:#f3f4f6;padding:1px 3px;border-radius:3px;font-size:12px">\1</code>',
                     content)
    content = content.replace("\n", "<br>")

    return f"""<html><body style="font-family:Arial,sans-serif;max-width:820px;margin:0 auto;padding:24px;color:#1f2937">
<h1 style="color:#1e3a8a;border-bottom:2px solid #e5e7eb;padding-bottom:12px;font-size:22px">
  DockerMind &mdash; Raport AI
</h1>
<table style="width:100%;border-collapse:collapse;margin-bottom:20px;font-size:14px">
  <tr><td style="padding:7px 12px;background:#f9fafb;font-weight:bold;width:130px">Kontener:</td>
      <td style="padding:7px 12px">{html_mod.escape(analysis.container_name)}</td></tr>
  <tr><td style="padding:7px 12px;background:#f9fafb;font-weight:bold">Obraz:</td>
      <td style="padding:7px 12px">{html_mod.escape(analysis.container_image or '\u2014')}</td></tr>
  <tr><td style="padding:7px 12px;background:#f9fafb;font-weight:bold">Ryzyko:</td>
      <td style="padding:7px 12px;color:{risk_hex};font-weight:bold">{analysis.risk_level}</td></tr>
  <tr><td style="padding:7px 12px;background:#f9fafb;font-weight:bold">Data analizy:</td>
      <td style="padding:7px 12px">{analysis.created_at.strftime('%Y-%m-%d %H:%M')}</td></tr>
  <tr><td style="padding:7px 12px;background:#f9fafb;font-weight:bold">CPU:</td>
      <td style="padding:7px 12px">{analysis.cpu_percent or 0}%</td></tr>
  <tr><td style="padding:7px 12px;background:#f9fafb;font-weight:bold">RAM:</td>
      <td style="padding:7px 12px">{analysis.mem_percent or 0}%</td></tr>
</table>
<div style="background:#f9fafb;padding:20px;border-radius:8px;border:1px solid #e5e7eb;font-size:14px;line-height:1.7">
  {content}
</div>
<p style="margin-top:20px;color:#9ca3af;font-size:11px">Wygenerowane przez DockerMind</p>
</body></html>"""


def _send_email(to: str, analysis, settings) -> None:
    """Synchronous SMTP send — run in executor from async context."""
    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"DockerMind \u2014 Raport AI: {analysis.container_name} [{analysis.risk_level}]"
    msg["From"]    = settings.SMTP_FROM or settings.SMTP_USER
    msg["To"]      = to

    msg.attach(MIMEText(_analysis_to_html(analysis), "html", "utf-8"))

    try:
        pdf_bytes = _generate_pdf(analysis)
        part = MIMEApplication(pdf_bytes, _subtype="pdf")
        part.add_header(
            "Content-Disposition",
            f'attachment; filename="dockermind-{analysis.container_name}-{analysis.id}.pdf"',
        )
        msg.attach(part)
    except Exception as pdf_err:
        logger.warning("PDF attachment skipped: %s", pdf_err)

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as smtp:
        if settings.SMTP_TLS:
            smtp.starttls()
        if settings.SMTP_USER and settings.SMTP_PASSWORD:
            smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        smtp.send_message(msg)
