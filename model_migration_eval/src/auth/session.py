"""
Flask session helpers — middleware and decorators for authentication.
"""

import functools
import logging
from typing import Optional

from flask import session, request, redirect, jsonify, g

logger = logging.getLogger(__name__)


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
    "/static/",
    "/favicon",
)


def is_public_route(path: str) -> bool:
    """Check if the request path is public (no auth needed)."""
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)
