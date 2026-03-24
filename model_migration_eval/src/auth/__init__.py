"""
Authentication & User Isolation Module
=======================================

Provides email-based OTP authentication, per-user content isolation,
and automatic sign-in when running behind Azure Container Apps EasyAuth.
"""

from .models import User
from .user_context import UserContext
from .session import (
    login_required,
    get_current_user_id,
    get_current_user_context,
    get_easyauth_email,
    get_easyauth_claims,
)

__all__ = [
    "User",
    "UserContext",
    "login_required",
    "get_current_user_id",
    "get_current_user_context",
    "get_easyauth_email",
    "get_easyauth_claims",
]
