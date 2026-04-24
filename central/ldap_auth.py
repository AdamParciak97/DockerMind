"""
ldap_auth.py — LDAP / Active Directory authentication for DockerMind.

Config priority:
  1. DB (LdapConfig table) — set via GUI in Settings → LDAP
  2. Environment variables (.env) — fallback if no DB record exists

Flow:
  1. Bind with service account (bind_dn / bind_password).
  2. Search for user DN using user_filter.
  3. Bind as the user with the supplied password.
  4. Determine role: "admin" if member of admin_group_dn, else "user".
"""

import logging
import ssl
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)


def _escape_ldap(value: str) -> str:
    """Escape LDAP filter special characters to prevent LDAP injection (RFC 4515)."""
    table = {
        "\\": "\\5c", "*": "\\2a", "(": "\\28",
        ")": "\\29", "\0": "\\00", "/": "\\2f",
    }
    return "".join(table.get(ch, ch) for ch in value)


def _get_ldap_settings() -> Optional[dict]:
    """
    Return effective LDAP settings as a dict.
    DB record takes priority; falls back to env vars.
    Returns None if LDAP is disabled in both sources.
    """
    try:
        from models import get_ldap_config, decrypt_secret
        from sqlmodel import Session
        from models import engine
        with Session(engine) as session:
            cfg = get_ldap_config(session)
        if cfg is not None:
            # DB record exists — use it regardless of env vars
            if not cfg.enabled:
                return None
            return {
                "server":         cfg.server,
                "port":           cfg.port,
                "use_ssl":        cfg.use_ssl,
                "use_tls":        cfg.use_tls,
                "tls_verify":     cfg.tls_verify,
                "bind_dn":        cfg.bind_dn,
                "bind_password":  decrypt_secret(cfg.bind_password_enc) if cfg.bind_password_enc else "",
                "base_dn":        cfg.base_dn,
                "user_filter":    cfg.user_filter,
                "admin_group_dn": cfg.admin_group_dn,
            }
    except Exception as e:
        logger.warning("Could not read LDAP config from DB: %s", e)

    # Fallback: environment variables
    if not settings.LDAP_ENABLED:
        return None
    if not settings.LDAP_SERVER or not settings.LDAP_BASE_DN:
        logger.warning("LDAP_ENABLED=true but LDAP_SERVER or LDAP_BASE_DN not configured.")
        return None
    return {
        "server":         settings.LDAP_SERVER,
        "port":           settings.LDAP_PORT,
        "use_ssl":        settings.LDAP_USE_SSL,
        "use_tls":        settings.LDAP_USE_TLS,
        "tls_verify":     settings.LDAP_TLS_VERIFY,
        "bind_dn":        settings.LDAP_BIND_DN,
        "bind_password":  settings.LDAP_BIND_PASSWORD,
        "base_dn":        settings.LDAP_BASE_DN,
        "user_filter":    settings.LDAP_USER_FILTER,
        "admin_group_dn": settings.LDAP_ADMIN_GROUP_DN,
    }


def _build_server(cfg: dict):
    """Build an ldap3 Server object from config dict."""
    from ldap3 import Server, Tls, ALL
    tls = None
    if cfg["use_ssl"] or cfg["use_tls"]:
        if not cfg["tls_verify"]:
            logger.warning(
                "LDAP TLS certificate verification is DISABLED for %s — "
                "set tls_verify=true in production.",
                cfg["server"],
            )
        validate = ssl.CERT_REQUIRED if cfg["tls_verify"] else ssl.CERT_NONE
        tls = Tls(validate=validate)
    return Server(
        cfg["server"],
        port=cfg["port"],
        use_ssl=cfg["use_ssl"],
        tls=tls,
        get_info=ALL,
    )


def ldap_authenticate(username: str, password: str) -> Optional[str]:
    """
    Authenticate against LDAP/AD.
    Returns "admin" or "user" on success, None on any failure.
    """
    cfg = _get_ldap_settings()
    if cfg is None:
        return None

    if not password or not password.strip():
        return None

    try:
        from ldap3 import Connection, SUBTREE
        from ldap3.core.exceptions import LDAPBindError, LDAPException
    except ImportError:
        logger.error("ldap3 is not installed.")
        return None

    try:
        server = _build_server(cfg)

        # Step 1: Search for user DN with service account
        safe_username = _escape_ldap(username)
        search_filter = cfg["user_filter"].replace("{username}", safe_username)

        with Connection(server, user=cfg["bind_dn"], password=cfg["bind_password"], auto_bind=True) as conn:
            conn.search(
                search_base=cfg["base_dn"],
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=["distinguishedName", "memberOf"],
            )
            if not conn.entries:
                logger.info("LDAP: user '%s' not found.", username)
                return None
            entry = conn.entries[0]
            user_dn = entry.entry_dn
            member_of: list[str] = []
            if hasattr(entry, "memberOf") and entry.memberOf:
                raw = entry.memberOf.value
                member_of = raw if isinstance(raw, list) else [raw]

        # Step 2: Bind as user to verify password
        try:
            with Connection(server, user=user_dn, password=password, auto_bind=True):
                pass
        except LDAPBindError:
            logger.info("LDAP: wrong password for '%s'.", username)
            return None

        # Step 3: Determine role
        role = "user"
        if cfg["admin_group_dn"]:
            admin_lower = cfg["admin_group_dn"].lower()
            if any(g.lower() == admin_lower for g in member_of):
                role = "admin"

        logger.info("LDAP: '%s' authenticated (role=%s).", username, role)
        return role

    except Exception as exc:
        logger.warning("LDAP auth failed for '%s': %s", username, type(exc).__name__)
        logger.debug("LDAP auth exception detail: %s", exc)
        return None


def test_ldap_service_bind(
    server: str, port: int, use_ssl: bool, use_tls: bool, tls_verify: bool,
    bind_dn: str, bind_password: str, base_dn: str, user_filter: str,
    test_username: str = "",
) -> dict:
    """
    Test LDAP connectivity:
    - Connects and binds with the service account.
    - Optionally searches for test_username and returns the found DN.
    Returns {"ok": True, "message": "..."} or {"ok": False, "error": "..."}.
    """
    try:
        from ldap3 import Connection, SUBTREE
        from ldap3.core.exceptions import LDAPException
    except ImportError:
        return {"ok": False, "error": "ldap3 nie jest zainstalowane w kontenerze."}

    cfg = {
        "server": server, "port": port, "use_ssl": use_ssl,
        "use_tls": use_tls, "tls_verify": tls_verify,
    }
    try:
        srv = _build_server(cfg)
        with Connection(srv, user=bind_dn, password=bind_password, auto_bind=True) as conn:
            if not test_username:
                return {"ok": True, "message": f"Połączenie z {server}:{port} udane. Konto serwisowe działa."}

            safe = _escape_ldap(test_username)
            search_filter = user_filter.replace("{username}", safe)
            conn.search(
                search_base=base_dn,
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=["distinguishedName"],
            )
            if conn.entries:
                dn = conn.entries[0].entry_dn
                return {"ok": True, "message": f"Znaleziono użytkownika: {dn}"}
            return {"ok": True, "message": f"Połączenie udane, ale użytkownik '{test_username}' nie znaleziony w {base_dn}."}

    except Exception as exc:
        logger.warning("LDAP test connection to %s failed: %s", server, exc)
        exc_type = type(exc).__name__
        # Return only the exception class name to avoid leaking credentials in messages
        return {"ok": False, "error": f"Błąd połączenia LDAP ({exc_type}). Sprawdź logi serwera."}
