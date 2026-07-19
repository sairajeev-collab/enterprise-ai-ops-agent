"""Real Slack adapter via Incoming Webhook.

This is the integration wired real end-to-end (ADR-0005): a single webhook URL,
no OAuth, posts genuine messages. Slack webhooks are not idempotent server-side,
so we keep a process-local guard against re-posting the same idempotency key.
Cross-process replay safety comes from the pipeline's ``run_step`` checkpointing.
"""

from __future__ import annotations

import httpx

from app.adapters.base import (
    NotifierPort,
    NotifyMessage,
    PermanentAdapterError,
    TransientAdapterError,
)
from app.logging import get_logger

logger = get_logger(__name__)


class SlackWebhookNotifier(NotifierPort):
    def __init__(
        self,
        *,
        webhook_url: str,
        default_channel: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not webhook_url:
            raise PermanentAdapterError(
                "SLACK_MODE=real requires SLACK_WEBHOOK_URL", code="slack_misconfigured"
            )
        self._webhook_url = webhook_url
        self._default_channel = default_channel
        self._client = client or httpx.AsyncClient(timeout=15.0)
        self._seen: set[str] = set()

    async def notify(self, message: NotifyMessage, *, idempotency_key: str) -> None:
        if idempotency_key in self._seen:
            logger.info("slack_notify_skipped_duplicate", extra={"key": idempotency_key})
            return

        payload: dict[str, object] = {"text": message.text}
        channel = message.channel or self._default_channel
        if channel:
            payload["channel"] = channel

        try:
            response = await self._client.post(self._webhook_url, json=payload)
        except httpx.TimeoutException as exc:
            raise TransientAdapterError("Slack request timed out", code="slack_timeout") from exc
        except httpx.TransportError as exc:
            raise TransientAdapterError("Slack transport error", code="slack_transport") from exc

        if response.status_code >= 500 or response.status_code == 429:
            raise TransientAdapterError(
                f"Slack upstream error: {response.status_code}", code="slack_upstream"
            )
        if response.status_code >= 400:
            raise PermanentAdapterError(
                f"Slack rejected message: {response.status_code} {response.text[:200]}",
                code="slack_client_error",
            )

        self._seen.add(idempotency_key)

    async def aclose(self) -> None:
        await self._client.aclose()
