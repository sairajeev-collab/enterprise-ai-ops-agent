"""Integration tests for the command-center refactor: SSE, list, batch, callbacks,
and the admin queue endpoints."""

from __future__ import annotations

import json

import httpx
import pytest
from app.adapters.llm.sandbox import SandboxLlm
from app.deps import Container

pytestmark = pytest.mark.integration

_OUTAGE = {
    "channel": "slack",
    "subject": "Outage",
    "body": "Production is down, we're seeing an outage across all regions right now.",
}


# --- Task 1: classification fix (unit) ------------------------------------- #
async def test_outage_classifies_as_technical_support() -> None:
    out = json.loads(
        await SandboxLlm().complete(system="TASK: classify", user=_OUTAGE["body"], temperature=0.0)
    )
    assert out["request_type"] == "technical_support"
    assert out["priority"] == "urgent"


# --- Task 3: SSE streaming -------------------------------------------------- #
async def test_stream_emits_node_events_and_completes(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    async with client.stream(
        "POST", "/v1/requests?inline=true&stream=true", json=_OUTAGE, headers=auth_headers
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        text = (await resp.aread()).decode()

    assert "event: node_delta" in text
    assert "event: complete" in text
    # The classification fix flows all the way through the stream.
    assert "technical_support" in text


# --- Task 4: list + batch --------------------------------------------------- #
async def test_list_requests_paginated(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    for _ in range(3):
        await client.post("/v1/requests?inline=true", json=_OUTAGE, headers=auth_headers)

    resp = await client.get("/v1/requests?limit=2", headers=auth_headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    assert {"id", "channel", "status"} <= set(rows[0])


async def test_batch_submission(
    client: httpx.AsyncClient, container: Container, auth_headers: dict[str, str]
) -> None:
    payload = {"requests": [_OUTAGE, {**_OUTAGE, "subject": "second"}]}
    resp = await client.post("/v1/requests/batch", json=payload, headers=auth_headers)
    assert resp.status_code == 202
    accepted = resp.json()
    assert len(accepted) == 2
    # Both were enqueued.
    assert await container.queue.depth() == 2
    # And both are fetchable.
    got = await client.get(f"/v1/requests/{accepted[0]['id']}", headers=auth_headers)
    assert got.status_code == 200


# --- Task 4.3: callback webhook -------------------------------------------- #
async def test_callback_fires_on_completion(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict]] = []

    class _CaptureClient:
        def __init__(self, *args: object, **kwargs: object) -> None: ...

        async def __aenter__(self) -> _CaptureClient:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def post(self, url: str, json: dict | None = None) -> None:
            calls.append((url, json or {}))

    monkeypatch.setattr("app.jobs.worker.httpx.AsyncClient", _CaptureClient)

    # Public IP literal: passes the SSRF egress guard (ADR-0021) and resolves to
    # itself, so the test needs no network. The POST itself is captured above.
    body = {**_OUTAGE, "callback_url": "https://8.8.8.8/done"}
    resp = await client.post("/v1/requests?inline=true", json=body, headers=auth_headers)
    assert resp.status_code == 202

    assert len(calls) == 1
    url, sent = calls[0]
    assert url == "https://8.8.8.8/done"
    assert sent["status"] == "completed"
    assert sent["request_type"] == "technical_support"


async def test_callback_url_must_be_http(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    body = {**_OUTAGE, "callback_url": "ftp://nope"}
    resp = await client.post("/v1/requests?inline=true", json=body, headers=auth_headers)
    assert resp.status_code == 422


async def test_callback_to_private_address_is_not_delivered(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Accepted at intake (syntactically valid http), but the fire-time SSRF guard
    # (ADR-0021) resolves the metadata IP as link-local and refuses to POST.
    calls: list[str] = []

    class _CaptureClient:
        def __init__(self, *args: object, **kwargs: object) -> None: ...

        async def __aenter__(self) -> _CaptureClient:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def post(self, url: str, json: dict | None = None) -> None:
            calls.append(url)

    monkeypatch.setattr("app.jobs.worker.httpx.AsyncClient", _CaptureClient)

    body = {**_OUTAGE, "callback_url": "http://169.254.169.254/latest/meta-data/"}
    resp = await client.post("/v1/requests?inline=true", json=body, headers=auth_headers)
    assert resp.status_code == 202
    assert calls == [], "the worker must not POST to a link-local metadata address"


# --- Task 5: admin queue endpoints ----------------------------------------- #
async def test_queue_insights(
    client: httpx.AsyncClient, container: Container, auth_headers: dict[str, str]
) -> None:
    await container.queue.enqueue("r-1")
    resp = await client.get("/system/queue", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"pending": 1, "processing": 0, "dead_letter": 0, "stuck": 0}


async def test_replay_from_dead_letter(
    client: httpx.AsyncClient, container: Container, auth_headers: dict[str, str]
) -> None:
    # Simulate a dead-lettered job by pushing straight onto the dead list.
    await container.redis.lpush("ops:jobs:dead", "dead-1")
    assert await container.queue.dead_letter_depth() == 1

    resp = await client.post("/system/queue/replay/dead-1", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"request_id": "dead-1", "requeued": True}
    assert await container.queue.dead_letter_depth() == 0
    assert await container.queue.depth() == 1

    # Replaying an id that isn't dead-lettered is a no-op.
    again = await client.post("/system/queue/replay/nope", headers=auth_headers)
    assert again.json()["requeued"] is False


# --- Task 2: UI is the command center -------------------------------------- #
async def test_ui_is_command_center(client: httpx.AsyncClient) -> None:
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "Ops Command Center" in resp.text
    assert "tailwindcss" in resp.text  # Tailwind via CDN
    assert "stream=true" in resp.text  # SSE path wired into the page
