"""Reliability & recovery: manual retry of failed jobs, queue insights."""

from __future__ import annotations

import httpx
import pytest
from app.db.engine import session_scope
from app.db.repository import Repository
from app.deps import Container
from app.domain.enums import RunStatus

pytestmark = pytest.mark.integration

_BODY = "I need a refund for my invoice urgently. from Jo Lee jo@x.io"


async def test_retry_requeues_a_failed_request(
    client: httpx.AsyncClient, container: Container, auth_headers: dict[str, str]
) -> None:
    async with session_scope(container.session_factory) as session:
        repo = Repository(session)
        created = await repo.create_request(channel="email", subject="s", body=_BODY)
        request_id = created.id
        await repo.update_request(created, status=RunStatus.FAILED, attempts=3, error="jira down")

    resp = await client.post(f"/v1/requests/{request_id}/retry", headers=auth_headers)
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"
    assert await container.queue.depth() == 1  # back on the queue

    async with session_scope(container.session_factory) as session:
        record = await Repository(session).get_request(request_id)
    assert record is not None
    assert record.status == RunStatus.QUEUED.value
    assert record.attempts == 0  # fresh set of attempts
    assert record.error is None


async def test_retry_rejects_non_failed(
    client: httpx.AsyncClient, container: Container, auth_headers: dict[str, str]
) -> None:
    async with session_scope(container.session_factory) as session:
        created = await Repository(session).create_request(channel="email", subject="s", body=_BODY)
        request_id = created.id  # status defaults to "queued"

    resp = await client.post(f"/v1/requests/{request_id}/retry", headers=auth_headers)
    assert resp.status_code == 422


async def test_retry_unknown_returns_404(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await client.post("/v1/requests/nope/retry", headers=auth_headers)
    assert resp.status_code == 404


async def test_queue_insights_includes_stuck_count(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await client.get("/system/queue", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["stuck"] == 0
    assert set(body) == {"pending", "processing", "dead_letter", "stuck"}
