"""In-memory sandbox notifier. Records messages instead of posting them."""

from __future__ import annotations

from app.adapters.base import NotifierPort, NotifyMessage
from app.logging import get_logger

logger = get_logger(__name__)


class SandboxNotifier(NotifierPort):
    def __init__(self) -> None:
        self.sent: list[NotifyMessage] = []
        self._seen: set[str] = set()

    async def notify(self, message: NotifyMessage, *, idempotency_key: str) -> None:
        if idempotency_key in self._seen:
            return
        self._seen.add(idempotency_key)
        self.sent.append(message)
        logger.info("sandbox_notify", extra={"text": message.text[:120]})
