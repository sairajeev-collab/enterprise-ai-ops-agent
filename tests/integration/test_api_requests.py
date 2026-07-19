"""Integration tests for the request intake/status API."""

from __future__ import annotations

import httpx
import pytest
from app.deps import Container
from app.jobs.worker import process_request

pytestmark = pytest.mark.integration

_BILLING = {
    "channel": "email",
    "subject": "Refund please",
    "body": "I need a refund for my invoice urgently. from Jane Smith jane@acme.com",
}


async def test_submit_inline_completes_and_exposes_artifacts(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await client.post("/v1/requests?inline=true", json=_BILLING, headers=auth_headers)
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "completed"
    request_id = body["id"]

    status = await client.get(f"/v1/requests/{request_id}", headers=auth_headers)
    assert status.status_code == 200
    data = status.json()
    assert data["request_type"] == "billing"
    kinds = {a["kind"] for a in data["artifacts"]}
    assert {"ticket", "reply", "notification", "report"} <= kinds

    report = await client.get(f"/v1/requests/{request_id}/report", headers=auth_headers)
    assert report.status_code == 200
    assert report.json()["report"]


async def test_submit_async_then_worker_processes(
    client: httpx.AsyncClient, container: Container, auth_headers: dict[str, str]
) -> None:
    resp = await client.post("/v1/requests", json=_BILLING, headers=auth_headers)
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"
    request_id = resp.json()["id"]

    # Simulate the worker draining the queue.
    queued_id = await container.queue.dequeue(timeout_seconds=1)
    assert queued_id == request_id
    await process_request(container, request_id)

    status = await client.get(f"/v1/requests/{request_id}", headers=auth_headers)
    assert status.json()["status"] == "completed"


async def test_requires_authentication(client: httpx.AsyncClient) -> None:
    resp = await client.post("/v1/requests?inline=true", json=_BILLING)
    assert resp.status_code == 401


async def test_unknown_request_returns_404(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await client.get("/v1/requests/does-not-exist", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_invalid_body_rejected(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await client.post(
        "/v1/requests?inline=true",
        json={"channel": "email", "body": ""},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_health_endpoint(client: httpx.AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
