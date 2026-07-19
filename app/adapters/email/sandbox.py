"""In-memory sandbox email adapter. Captures outbound mail for inspection."""

from __future__ import annotations

import uuid

from app.adapters.base import EmailMessage, EmailPort, EmailReceipt
from app.logging import get_logger

logger = get_logger(__name__)


class SandboxEmail(EmailPort):
    def __init__(self) -> None:
        self.outbox: list[EmailMessage] = []
        self._by_key: dict[str, EmailReceipt] = {}

    async def send(self, message: EmailMessage, *, idempotency_key: str) -> EmailReceipt:
        if idempotency_key in self._by_key:
            return self._by_key[idempotency_key]
        receipt = EmailReceipt(
            message_id=f"<{uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key)}@sandbox>"
        )
        self._by_key[idempotency_key] = receipt
        self.outbox.append(message)
        logger.info("sandbox_email", extra={"to": message.to, "subject": message.subject})
        return receipt
