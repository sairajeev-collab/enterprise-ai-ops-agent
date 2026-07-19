"""End-to-end pipeline tests over sandbox adapters (no infrastructure)."""

from __future__ import annotations

import pytest
from app.domain.enums import RequestType, RunStatus
from app.domain.state import AgentState
from app.graph.build import run_pipeline
from app.graph.context import NodeContext

pytestmark = pytest.mark.integration


def _state(subject: str, body: str) -> AgentState:
    return AgentState(request_id="e2e-1", channel="email", raw_subject=subject, raw_body=body)


async def test_happy_path_runs_all_nodes(sandbox_ctx: NodeContext) -> None:
    state = _state(
        "Refund request",
        "I need a refund for my invoice urgently. from Jane Smith jane@acme.com",
    )
    final = await run_pipeline(sandbox_ctx, state)

    assert final.status is RunStatus.COMPLETED
    assert final.classification is not None
    assert final.classification.request_type is RequestType.BILLING
    assert final.ticket is not None and final.ticket.key
    assert final.knowledge, "expected knowledge grounding"
    assert final.reply is not None and final.reply.sent is True
    assert final.notification_sent is True
    assert final.summary_record is not None
    assert final.report


async def test_low_confidence_routes_to_review_without_side_effects(
    sandbox_ctx: NodeContext,
) -> None:
    final = await run_pipeline(sandbox_ctx, _state("Hi", "hello there friend, nice weather"))

    assert final.status is RunStatus.NEEDS_REVIEW
    assert final.review_reason is not None
    # Guard: no irreversible actions taken on an unclassifiable request.
    assert final.ticket is None
    assert final.reply is None
    assert final.notification_sent is False


async def test_pipeline_is_idempotent_on_replay(sandbox_ctx: NodeContext) -> None:
    # Re-running with the same request id must not create a second ticket.
    state = _state("Login issue", "I cannot login, password reset broken. from Al Bee al@x.io")
    first = await run_pipeline(sandbox_ctx, state)
    second = await run_pipeline(sandbox_ctx, _state("Login issue", state.raw_body))

    assert first.ticket is not None and second.ticket is not None
    assert first.ticket.key == second.ticket.key
