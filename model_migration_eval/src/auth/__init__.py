"""
Authentication & User Isolation Module
=======================================

Provides email-based OTP authentication and per-user content isolation.
"""

from .models import User
from .user_context import UserContext
from .session import login_required, get_current_user_id, get_current_user_context

__all__ = [
    "User",
    "UserContext",
    "login_required",
    "get_current_user_id",
    "get_current_user_context",
]
