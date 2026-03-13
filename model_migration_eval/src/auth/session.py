"""
Flask session helpers — middleware and decorators for authentication.

Includes support for Azure Container Apps EasyAuth: when the app runs
behind the built-in authentication sidecar, user identity is conveyed
via ``X-MS-CLIENT-PRINCIPAL-NAME`` (and related headers).  The helper
:func:`get_easyauth_email` extracts the email so the app can create a
Flask session automatically, skipping the manual login page.
"""

import base64
import functools
import json
import logging
from typing import Optional

from flask import session, request, redirect, jsonify, g

logger = logging.getLogger(__name__)


# ── EasyAuth helpers ─────────────────────────────────────────────────

def get_easyauth_email() -> Optional[str]:
    """Return the authenticated user's email from Azure Container Apps
    EasyAuth headers, or ``None`` if not running behind EasyAuth.

    The sidecar injects ``X-MS-CLIENT-PRINCIPAL-NAME`` with the user's
    email/UPN on every authenticated request.  External callers cannot
    forge this header — the sidecar strips it from inbound traffic.
    """
    principal_name = (request.headers.get("X-MS-CLIENT-PRINCIPAL-NAME") or "").strip()
    if principal_name and "@" in principal_name:
        return principal_name.lower()
    return None


def get_easyauth_claims() -> Optional[dict]:
    """Decode the full ``X-MS-CLIENT-PRINCIPAL`` header (Base64 JSON).

    Returns the parsed dict of claims, or ``None`` if the header is
    absent or malformed.  Useful for extracting roles, name, etc.
    """
    raw = request.headers.get("X-MS-CLIENT-PRINCIPAL")
    if not raw:
        return None
    try:
        decoded = base64.b64decode(raw)
        return json.loads(decoded)
    except Exception:
        logger.debug("Failed to decode X-MS-CLIENT-PRINCIPAL header")
        return None


# ── Session helpers ──────────────────────────────────────────────────

def get_current_user_id() -> Optional[str]:
    """Return the authenticated user_id from the Flask session, or None."""
    return session.get("user_id")


def get_current_user_context():
    """Return the UserContext for the current request (cached in ``g``)."""
    return getattr(g, "_user_context", None)


def login_required(f):
    """Decorator that protects a route — returns 401 for API calls,
    redirects to /login for page requests."""

    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not get_current_user_id():
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return redirect("/login")
        return f(*args, **kwargs)

    return decorated


# ── Public routes that skip authentication ───────────────────────────

PUBLIC_PREFIXES = (
    "/login",
    "/api/auth/",
    "/api/health",
    "/.auth/",         # Azure Container Apps EasyAuth callback routes
    "/static/",
    "/favicon",
)


def is_public_route(path: str) -> bool:
    """Check if the request path is public (no auth needed)."""
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)
