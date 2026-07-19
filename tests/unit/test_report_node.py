"""Unit tests for the report and needs-review terminal nodes."""

from __future__ import annotations

from app.domain.enums import Priority, RequestType, RunStatus
from app.domain.state import AgentState, Classification
from app.graph.nodes import needs_review_node, report_node

from tests.unit.fakes import make_ctx


def _state() -> AgentState:
    state = AgentState(request_id="req-1", channel="email", raw_body="help")
    state.classification = Classification(
        request_type=RequestType.OTHER, priority=Priority.MEDIUM, confidence=0.2
    )
    return state


async def test_report_sets_completed_status() -> None:
    ctx = make_ctx()
    state = _state()
    state.summary_record = {"request_id": "req-1", "ticket_key": "OPS-99"}
    delta = await report_node(state, ctx)
    assert delta["status"] is RunStatus.COMPLETED
    assert isinstance(delta["report"], str) and delta["report"]


async def test_needs_review_sets_status_and_reason() -> None:
    ctx = make_ctx()
    delta = await needs_review_node(_state(), ctx)
    assert delta["status"] is RunStatus.NEEDS_REVIEW
    assert "0.20" in delta["review_reason"]
