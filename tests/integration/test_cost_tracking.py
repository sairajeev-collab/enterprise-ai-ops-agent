"""Cost tracking: per-run logging, the budget circuit breaker, and the report."""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
from app.cost import LlmUsage
from app.db.engine import session_scope
from app.db.repository import Repository
from app.deps import Container
from app.jobs.worker import _select_pipeline, process_request

pytestmark = pytest.mark.integration

_REQUEST = {
    "channel": "email",
    "subject": "Refund",
    "body": "I need a refund for my invoice urgently. from Jane Smith jane@acme.com",
}


async def _new_request(container: Container) -> str:
    async with session_scope(container.session_factory) as session:
        created = await Repository(session).create_request(
            channel="email", subject="s", body=_REQUEST["body"]
        )
        return created.id


async def test_run_logs_llm_calls(container: Container) -> None:
    request_id = await _new_request(container)
    await process_request(container, request_id)

    async with session_scope(container.session_factory) as session:
        rows = await Repository(session).cost_rows_since(
            dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
        )
        request = await Repository(session).get_request(request_id)

    calls = [r for r in rows if r.request_id == request_id]
    assert calls, "expected at least one LLM call logged"
    assert all(c.model == "sandbox" for c in calls)  # sandbox mode
    assert request is not None and request.cost_usd == 0.0  # sandbox is free


async def test_budget_cap_trips_to_degraded_pipeline(container: Container) -> None:
    # Log a spend row above the cap, then confirm the selector degrades.
    request_id = await _new_request(container)
    async with session_scope(container.session_factory) as session:
        await Repository(session).add_llm_calls(
            request_id,
            "billing",
            [
                LlmUsage(
                    provider="openai",
                    model="gpt-4o",
                    tokens_in=0,
                    tokens_out=0,
                    cost_usd=1.0,
                    latency_ms=1,
                )
            ],
        )

    container.settings.daily_budget_cap_usd = 0.5
    assert await _select_pipeline(container) is container.degraded_pipeline

    container.settings.daily_budget_cap_usd = 100.0
    assert await _select_pipeline(container) is container.pipeline


async def test_cost_report_endpoint(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    await client.post("/v1/requests?inline=true", json=_REQUEST, headers=auth_headers)

    resp = await client.get("/metrics/costs?days=1", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["calls"] > 0
    assert "sandbox" in body["by_model"]
    assert body["total_usd"] == 0.0  # sandbox mode


async def test_cost_report_requires_auth(client: httpx.AsyncClient) -> None:
    resp = await client.get("/metrics/costs")
    assert resp.status_code == 401
