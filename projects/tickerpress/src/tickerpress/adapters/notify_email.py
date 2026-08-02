"""Live notifier: email over SMTP + STARTTLS (SCOPE FR-12).

Credential-gated: :meth:`EmailNotifier.from_env` returns ``None`` unless every
required variable is present, so the CLI can offer the channel without ever
half-configuring it. Tests and evals use the offline notifiers exclusively.

Environment:

``TICKERPRESS_SMTP_HOST``     SMTP server host (required)
``TICKERPRESS_SMTP_PORT``     SMTP port (default 587)
``TICKERPRESS_SMTP_USERNAME`` login user (optional; anonymous relay if unset)
``TICKERPRESS_SMTP_PASSWORD`` login password (required with a username)
``TICKERPRESS_EMAIL_FROM``    envelope + header From (required)
``TICKERPRESS_EMAIL_TO``      comma-separated recipients (required)
"""

from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from .notify import ComposedMessage, DeliveryError

__all__ = ["EmailNotifier"]

ENV_HOST = "TICKERPRESS_SMTP_HOST"
ENV_PORT = "TICKERPRESS_SMTP_PORT"
ENV_USERNAME = "TICKERPRESS_SMTP_USERNAME"
ENV_PASSWORD = "TICKERPRESS_SMTP_PASSWORD"
ENV_FROM = "TICKERPRESS_EMAIL_FROM"
ENV_TO = "TICKERPRESS_EMAIL_TO"


@dataclass(frozen=True, slots=True)
class EmailNotifier:
    """Send the rendered Markdown body as a plain-text email."""

    host: str
    sender: str
    recipients: tuple[str, ...]
    port: int = 587
    username: str | None = None
    password: str | None = None
    timeout: float = 20.0

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> EmailNotifier | None:
        """Build from environment, or ``None`` when not configured."""

        source = os.environ if env is None else env
        host = source.get(ENV_HOST, "").strip()
        sender = source.get(ENV_FROM, "").strip()
        recipients = tuple(
            part.strip() for part in source.get(ENV_TO, "").split(",") if part.strip()
        )
        if not host or not sender or not recipients:
            return None
        try:
            port = int(source.get(ENV_PORT, "587"))
        except ValueError as exc:
            raise DeliveryError(f"invalid {ENV_PORT}: {source.get(ENV_PORT)!r}") from exc
        username = source.get(ENV_USERNAME, "").strip() or None
        password = source.get(ENV_PASSWORD, "").strip() or None
        if username and not password:
            raise DeliveryError(f"{ENV_USERNAME} is set but {ENV_PASSWORD} is not")
        return cls(
            host=host,
            sender=sender,
            recipients=recipients,
            port=port,
            username=username,
            password=password,
        )

    def build_message(self, message: ComposedMessage) -> EmailMessage:
        mail = EmailMessage()
        mail["Subject"] = message.subject
        mail["From"] = self.sender
        mail["To"] = ", ".join(self.recipients)
        mail.set_content(message.body_text)
        return mail

    def deliver(self, message: ComposedMessage) -> None:
        mail = self.build_message(message)
        try:  # pragma: no cover - requires a live SMTP server
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as client:
                client.ehlo()
                client.starttls()
                client.ehlo()
                if self.username and self.password:
                    client.login(self.username, self.password)
                client.send_message(mail, from_addr=self.sender, to_addrs=list(self.recipients))
        except (OSError, smtplib.SMTPException) as exc:  # pragma: no cover - live only
            raise DeliveryError(f"email delivery failed: {exc}") from exc
