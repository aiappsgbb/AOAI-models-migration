"""
Email sender — sends OTP codes to users.

Supports multiple backends:
  - **console**: Prints the code to stdout/logs (development).
  - **smtp**: Uses Python's built-in smtplib.

The backend is selected via ``settings.yaml → auth.email_provider``.
"""

import logging
import smtplib
from abc import ABC, abstractmethod
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class EmailSender(ABC):
    """Abstract base class for email sending."""

    @abstractmethod
    def send_code(self, to_email: str, code: str) -> bool:
        """Send the OTP *code* to *to_email*.  Returns True on success."""
        ...


class ConsoleEmailSender(EmailSender):
    """Development backend — prints the code to the log."""

    def send_code(self, to_email: str, code: str) -> bool:
        msg = (
            f"\n{'='*50}\n"
            f"  [DEV] OTP code for {to_email}: {code}\n"
            f"{'='*50}"
        )
        logger.warning(msg)
        print(msg)
        return True


class SmtpEmailSender(EmailSender):
    """Production backend — sends email via SMTP."""

    def __init__(
        self,
        host: str,
        port: int = 587,
        username: str = "",
        password: str = "",
        sender: str = "",
        use_tls: bool = True,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sender = sender or username
        self.use_tls = use_tls

    def send_code(self, to_email: str, code: str) -> bool:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"Your verification code: {code}"
            msg["From"] = self.sender
            msg["To"] = to_email

            text = (
                f"Your verification code is: {code}\n\n"
                f"This code expires in 5 minutes.\n"
                f"If you didn't request this, please ignore this email."
            )
            html = f"""\
<html>
<body style="font-family: 'Segoe UI', Tahoma, sans-serif; max-width: 480px; margin: 0 auto; padding: 24px;">
    <div style="text-align: center; padding: 24px 0;">
        <h2 style="color: #0078D4; margin-bottom: 8px;">Model Migration Evaluator</h2>
        <p style="color: #605E5C; font-size: 14px;">Verification Code</p>
    </div>
    <div style="background: #F3F2F1; border-radius: 8px; padding: 24px; text-align: center; margin: 16px 0;">
        <span style="font-size: 32px; font-weight: 700; letter-spacing: 8px; color: #323130;">{code}</span>
    </div>
    <p style="color: #605E5C; font-size: 13px; text-align: center;">
        This code expires in 5 minutes.<br>
        If you didn't request this, please ignore this email.
    </p>
</body>
</html>"""

            msg.attach(MIMEText(text, "plain"))
            msg.attach(MIMEText(html, "html"))

            with smtplib.SMTP(self.host, self.port, timeout=15) as server:
                if self.use_tls:
                    server.starttls()
                if self.username and self.password:
                    server.login(self.username, self.password)
                server.send_message(msg)

            logger.info(f"OTP email sent to {to_email} via SMTP ({self.host})")
            return True

        except Exception as e:
            logger.error(f"Failed to send OTP email to {to_email}: {e}")
            return False


def create_email_sender(config: Optional[Dict] = None) -> EmailSender:
    """Factory: create the appropriate EmailSender from ``settings.yaml`` config.

    Config structure (``auth`` section)::

        auth:
          email_provider: "smtp"   # or "console"
          smtp:
            host: "smtp.office365.com"
            port: 587
            username: "${SMTP_USERNAME}"
            password: "${SMTP_PASSWORD}"
            sender: "noreply@example.com"
    """
    if not config:
        logger.warning("No auth config provided — falling back to console email sender")
        return ConsoleEmailSender()

    auth_cfg = config.get("auth", {})
    provider = auth_cfg.get("email_provider", "console").lower()

    if provider == "smtp":
        smtp_cfg = auth_cfg.get("smtp", {})
        host = _resolve_env(smtp_cfg.get("host", ""))
        if not host:
            logger.warning("SMTP host not configured — falling back to console email sender")
            return ConsoleEmailSender()
        return SmtpEmailSender(
            host=host,
            port=int(smtp_cfg.get("port", 587)),
            username=_resolve_env(smtp_cfg.get("username", "")),
            password=_resolve_env(smtp_cfg.get("password", "")),
            sender=_resolve_env(smtp_cfg.get("sender", "")),
            use_tls=smtp_cfg.get("use_tls", True),
        )

    # Default: console
    return ConsoleEmailSender()


def _resolve_env(value: str) -> str:
    """Resolve ``${ENV_VAR}`` placeholders in config values."""
    import os
    import re

    if not value or not isinstance(value, str):
        return value or ""
    def _replacer(m):
        return os.environ.get(m.group(1), "")
    return re.sub(r"\$\{(\w+)\}", _replacer, value)
