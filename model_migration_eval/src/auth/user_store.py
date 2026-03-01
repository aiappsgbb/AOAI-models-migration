"""
User store — SQLite-backed user registration and lookup.
"""

import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import User

import logging

logger = logging.getLogger(__name__)


def _email_to_slug(email: str) -> str:
    """Convert an email address to a filesystem-safe slug.

    Example: ``angel.garcia@microsoft.com`` → ``angel_garcia_at_microsoft_com``
    """
    s = email.lower().strip()
    s = s.replace("@", "_at_")
    s = re.sub(r"[^\w]", "_", s)
    s = re.sub(r"_+", "_", s)
    return s.strip("_")


class UserStore:
    """Thread-safe SQLite store for user accounts.

    The database file is created automatically on first access.
    """

    def __init__(self, db_path: str = "data/auth.db"):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        # Initialise schema once
        self._init_schema()

    # ── Connection per thread ────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def _init_schema(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id    TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                last_login TEXT
            );
        """)
        conn.commit()

    # ── Public API ───────────────────────────────────────────────────

    def get_or_create(self, email: str) -> User:
        """Return the existing user for *email*, or create a new one."""
        slug = _email_to_slug(email)
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (slug,)).fetchone()
        if row:
            return User(
                id=row["id"],
                email=row["email"],
                created_at=row["created_at"],
                last_login=row["last_login"] or "",
            )
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO users (id, email, created_at, last_login) VALUES (?, ?, ?, ?)",
            (slug, email.lower().strip(), now, now),
        )
        conn.commit()
        logger.info(f"New user created: {slug} ({email})")
        return User(id=slug, email=email.lower().strip(), created_at=now, last_login=now)

    def get_by_email(self, email: str) -> Optional[User]:
        slug = _email_to_slug(email)
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (slug,)).fetchone()
        if not row:
            return None
        return User(
            id=row["id"],
            email=row["email"],
            created_at=row["created_at"],
            last_login=row["last_login"] or "",
        )

    def update_last_login(self, user_id: str):
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, user_id))
        conn.commit()

    @staticmethod
    def email_to_slug(email: str) -> str:
        """Public helper to convert email → user_id slug."""
        return _email_to_slug(email)
