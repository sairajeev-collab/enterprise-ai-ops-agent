"""Real Jira Cloud adapter (REST API v3).

Jira has no native idempotency key, so we implement one: every ticket we create
carries a label equal to the caller's ``idempotency_key``. Before creating, we
search for that label and return the existing issue if present. This makes the
node safe to replay after a mid-run crash without spawning duplicate tickets.

Descriptions are sent as Atlassian Document Format (ADF), which v3 requires.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from app.adapters.base import (
    PermanentAdapterError,
    TicketPort,
    TicketRef,
    TicketRequest,
    TransientAdapterError,
)
from app.logging import get_logger

logger = get_logger(__name__)


class JiraRestTickets(TicketPort):
    def __init__(
        self,
        *,
        base_url: str,
        email: str,
        api_token: str,
        project_key: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._project_key = project_key
        token = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Basic {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def create_ticket(self, ticket: TicketRequest, *, idempotency_key: str) -> TicketRef:
        existing = await self._find_by_label(idempotency_key)
        if existing is not None:
            logger.info("jira_ticket_reused", extra={"key": existing.key})
            return existing

        payload: dict[str, Any] = {
            "fields": {
                "project": {"key": self._project_key},
                "summary": ticket.summary,
                "issuetype": {"name": ticket.issue_type},
                "labels": [*ticket.labels, idempotency_key],
                "description": _to_adf(ticket.description),
            }
        }
        data = await self._request("POST", "/rest/api/3/issue", json=payload)
        key = str(data["key"])
        return TicketRef(key=key, url=self._browse_url(key))

    async def _find_by_label(self, label: str) -> TicketRef | None:
        jql = f'project = "{self._project_key}" AND labels = "{label}"'
        data = await self._request(
            "GET", "/rest/api/3/search", params={"jql": jql, "maxResults": 1, "fields": "key"}
        )
        issues = data.get("issues") or []
        if not issues:
            return None
        key = str(issues[0]["key"])
        return TicketRef(key=key, url=self._browse_url(key))

    def _browse_url(self, key: str) -> str:
        return f"{str(self._client.base_url).rstrip('/')}/browse/{key}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = await self._client.request(method, path, json=json, params=params)
        except httpx.TimeoutException as exc:
            raise TransientAdapterError("Jira request timed out", code="jira_timeout") from exc
        except httpx.TransportError as exc:
            raise TransientAdapterError("Jira transport error", code="jira_transport") from exc

        if response.status_code >= 500 or response.status_code == 429:
            raise TransientAdapterError(
                f"Jira upstream error: {response.status_code}", code="jira_upstream"
            )
        if response.status_code >= 400:
            raise PermanentAdapterError(
                f"Jira rejected request: {response.status_code} {response.text[:200]}",
                code="jira_client_error",
            )
        return response.json() if response.content else {}

    async def aclose(self) -> None:
        await self._client.aclose()


def _to_adf(text: str) -> dict[str, object]:
    """Wrap plain text in a minimal Atlassian Document Format document."""

    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": text}]},
        ],
    }
