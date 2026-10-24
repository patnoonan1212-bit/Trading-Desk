"""Alerts by email.

Set up once (GitHub: Settings -> Secrets and variables -> Actions):
    ALERT_EMAIL_TO    where alerts go (your email address)
    SMTP_USERNAME     the Gmail address that sends them (can be the same as ALERT_EMAIL_TO)
    SMTP_PASSWORD     a Gmail "app password" for that address (not your normal password)
Other providers work too: set SMTP_HOST and SMTP_PORT (default smtp.gmail.com, 465).

What gets sent is set by `paper.notify` (trades / alerts / none), plus one summary at the end of each trading day.
If email isn't set up, or sending fails, the message is posted on the status issue instead, so nothing is lost.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger(__name__)


class EmailAlerter:
    def __init__(self, to: str | None = None, username: str | None = None, password: str | None = None,
                 host: str | None = None, port: int | None = None, smtp_factory=None) -> None:
        self.to = to or os.environ.get("ALERT_EMAIL_TO", "")
        self.username = username or os.environ.get("SMTP_USERNAME", "")
        self.password = password or os.environ.get("SMTP_PASSWORD", "")
        self.host = host or os.environ.get("SMTP_HOST", "smtp.gmail.com")
        self.port = int(port or os.environ.get("SMTP_PORT", 465))
        self._smtp = smtp_factory or (lambda: smtplib.SMTP_SSL(self.host, self.port, context=ssl.create_default_context(), timeout=30))
        self.last_error = ""

    @property
    def configured(self) -> bool:
        return bool(self.to and self.username and self.password)

    def send(self, subject: str, body: str) -> bool:
        """Send a plain-text email. Returns False (and remembers why) instead of raising."""
        if not self.configured:
            self.last_error = "email isn't set up (ALERT_EMAIL_TO, SMTP_USERNAME, SMTP_PASSWORD)"
            return False
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = f"Trading desk <{self.username}>"
        msg["To"] = self.to
        msg.set_content(body)
        try:
            with self._smtp() as smtp:
                smtp.login(self.username, self.password)
                smtp.send_message(msg)
            self.last_error = ""
            return True
        except (smtplib.SMTPException, OSError) as e:
            self.last_error = f"sending failed: {e.__class__.__name__}: {str(e)[:120]}"
            log.warning("Email alert failed: %s", self.last_error)
            return False


def plain(markdown: str) -> str:
    """Markdown from the status/notify pages, made readable as plain email text."""
    return markdown.replace("**", "").replace("`", "")
