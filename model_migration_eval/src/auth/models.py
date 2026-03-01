"""
User model for authentication.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class User:
    """Represents an authenticated user."""
    id: str  # slug derived from email, e.g. "angel_garcia_at_microsoft_com"
    email: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_login: str = ""
