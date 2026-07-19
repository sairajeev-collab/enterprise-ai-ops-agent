"""Real email adapter over SMTP.

Uses the stdlib ``smtplib`` (STARTTLS) run in a thread via ``asyncio.to_thread``
so the blocking socket work does not stall the event loop, and we avoid taking on
another dependency for a well-solved problem. The idempotency key is emitted as a
custom header so a downstream mail system (or a human) can spot duplicates.
"""

from __future__ import annotations

import asyncio
import smtplib
import uuid
from email.message import EmailMessage as MimeMessage

from app.adapters.base import (
    EmailMessage,
    EmailPort,
    EmailReceipt,
    PermanentAdapterError,
    TransientAdapterError,
)
from app.logging import get_logger

logger = get_logger(__name__)


class SmtpEmail(EmailPort):
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        sender: str,
    ) -> None:
        if not host:
            raise PermanentAdapterError(
                "EMAIL_MODE=real requires SMTP_HOST", code="email_misconfigured"
            )
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender

    async def send(self, message: EmailMessage, *, idempotency_key: str) -> EmailReceipt:
        message_id = f"<{uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key)}@ops-agent>"
        mime = MimeMessage()
        mime["From"] = self._sender
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime["Message-ID"] = message_id
        mime["X-Idempotency-Key"] = idempotency_key
        mime.set_content(message.body)

        try:
            await asyncio.to_thread(self._send_blocking, mime)
        except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, TimeoutError) as exc:
            raise TransientAdapterError("SMTP connection failed", code="email_transport") from exc
        except smtplib.SMTPResponseException as exc:
            # 4xx SMTP codes are transient; 5xx are permanent.
            if 400 <= exc.smtp_code < 500:
                raise TransientAdapterError(
                    f"SMTP temporary failure {exc.smtp_code}", code="email_temporary"
                ) from exc
            raise PermanentAdapterError(
                f"SMTP permanent failure {exc.smtp_code}", code="email_rejected"
            ) from exc
        except smtplib.SMTPException as exc:
            raise PermanentAdapterError(f"SMTP error: {exc}", code="email_error") from exc

        return EmailReceipt(message_id=message_id)

    def _send_blocking(self, mime: MimeMessage) -> None:
        with smtplib.SMTP(self._host, self._port, timeout=30) as server:
            server.starttls()
            if self._username:
                server.login(self._username, self._password)
            server.send_message(mime)
