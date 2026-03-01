"""
OTP code manager — generates, stores, and validates one-time codes.

Uses SQLite for atomic operations with TTL and attempt limiting.
"""

import hashlib
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

import logging

logger = logging.getLogger(__name__)


class CodeManager:
    """Manages OTP codes with TTL and rate limiting.

    Parameters:
        db_path: Path to the SQLite database (shared with UserStore).
        code_length: Number of digits in the OTP code.
        ttl_seconds: How long a code remains valid.
        max_attempts: Maximum verification attempts before the code is invalidated.
    """

    def __init__(
        self,
        db_path: str = "data/auth.db",
        code_length: int = 6,
        ttl_seconds: int = 300,
        max_attempts: int = 3,
    ):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self.code_length = code_length
        self.ttl_seconds = ttl_seconds
        self.max_attempts = max_attempts
        self._local = threading.local()
        self._init_schema()

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
            CREATE TABLE IF NOT EXISTS auth_codes (
                email      TEXT NOT NULL,
                code_hash  TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                attempts   INTEGER DEFAULT 0,
                used       INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_auth_codes_email
                ON auth_codes (email, used, expires_at);
        """)
        conn.commit()

    @staticmethod
    def _hash(code: str) -> str:
        return hashlib.sha256(code.encode()).hexdigest()

    def generate(self, email: str) -> str:
        """Create a new OTP code for *email* and return the plaintext code.

        Any previous unused codes for this email are invalidated.
        """
        email = email.lower().strip()
        conn = self._get_conn()

        # Invalidate old codes
        conn.execute(
            "UPDATE auth_codes SET used = 1 WHERE email = ? AND used = 0",
            (email,),
        )

        code = "".join(str(secrets.randbelow(10)) for _ in range(self.code_length))
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=self.ttl_seconds)

        conn.execute(
            "INSERT INTO auth_codes (email, code_hash, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (email, self._hash(code), now.isoformat(), expires.isoformat()),
        )
        conn.commit()
        logger.info(f"OTP code generated for {email}")
        return code

    def verify(self, email: str, code: str) -> Tuple[bool, str]:
        """Verify an OTP code.

        Returns:
            (success: bool, message: str)
        """
        email = email.lower().strip()
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()

        row = conn.execute(
            """SELECT rowid, * FROM auth_codes
               WHERE email = ? AND used = 0 AND expires_at > ?
               ORDER BY created_at DESC LIMIT 1""",
            (email, now),
        ).fetchone()

        if not row:
            return False, "No valid code found. Please request a new one."

        if row["attempts"] >= self.max_attempts:
            conn.execute("UPDATE auth_codes SET used = 1 WHERE rowid = ?", (row["rowid"],))
            conn.commit()
            return False, "Too many attempts. Please request a new code."

        if self._hash(code) != row["code_hash"]:
            conn.execute(
                "UPDATE auth_codes SET attempts = attempts + 1 WHERE rowid = ?",
                (row["rowid"],),
            )
            conn.commit()
            remaining = self.max_attempts - row["attempts"] - 1
            return False, f"Invalid code. {remaining} attempt(s) remaining."

        # Success — mark as used
        conn.execute("UPDATE auth_codes SET used = 1 WHERE rowid = ?", (row["rowid"],))
        conn.commit()
        logger.info(f"OTP code verified for {email}")
        return True, "OK"

    def cleanup_expired(self):
        """Remove codes that have expired (housekeeping)."""
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("DELETE FROM auth_codes WHERE expires_at < ?", (now,))
        conn.commit()
        conn.commit()
