"""
exchange.py — Send emails via Microsoft Graph API (Exchange Online / Microsoft 365).

Requirements in Azure AD:
  1. App Registration → Certificates & secrets → New client secret
  2. API permissions → Microsoft Graph → Application → Mail.Send → Grant admin consent
  3. Set EXCHANGE_SENDER to the mailbox from which emails are sent

No extra Python dependencies — uses httpx which is already in requirements.txt.
"""

import base64
import logging
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
_SEND_URL  = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"
_SCOPE     = "https://graph.microsoft.com/.default"


async def _get_token() -> str:
    """Obtain an OAuth2 access token via client credentials flow."""
    url = _TOKEN_URL.format(tenant_id=settings.EXCHANGE_TENANT_ID)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, data={
            "grant_type":    "client_credentials",
            "client_id":     settings.EXCHANGE_CLIENT_ID,
            "client_secret": settings.EXCHANGE_CLIENT_SECRET,
            "scope":         _SCOPE,
        })
        resp.raise_for_status()
        return resp.json()["access_token"]


async def send_via_exchange(
    to: str,
    subject: str,
    html_body: str,
    attachment_bytes: Optional[bytes] = None,
    attachment_name: Optional[str] = None,
) -> None:
    """
    Send an email via Microsoft Graph API.
    Raises httpx.HTTPStatusError on failure.
    """
    if not all([
        settings.EXCHANGE_TENANT_ID,
        settings.EXCHANGE_CLIENT_ID,
        settings.EXCHANGE_CLIENT_SECRET,
        settings.EXCHANGE_SENDER,
    ]):
        raise RuntimeError(
            "Exchange nie jest w pełni skonfigurowany. "
            "Sprawdź EXCHANGE_TENANT_ID, EXCHANGE_CLIENT_ID, "
            "EXCHANGE_CLIENT_SECRET i EXCHANGE_SENDER w .env."
        )

    token = await _get_token()

    message: dict = {
        "subject": subject,
        "body": {
            "contentType": "HTML",
            "content": html_body,
        },
        "toRecipients": [
            {"emailAddress": {"address": to}}
        ],
    }

    if attachment_bytes and attachment_name:
        message["attachments"] = [{
            "@odata.type":  "#microsoft.graph.fileAttachment",
            "name":          attachment_name,
            "contentType":   "application/pdf",
            "contentBytes":  base64.b64encode(attachment_bytes).decode(),
        }]

    url = _SEND_URL.format(sender=settings.EXCHANGE_SENDER)
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            url,
            json={"message": message, "saveToSentItems": True},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()

    logger.info("Exchange: email sent to '%s' via Graph API.", to)
