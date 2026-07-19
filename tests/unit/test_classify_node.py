"""Unit tests for the classify node and its routing."""

from __future__ import annotations

import pytest
from app.adapters.base import LlmPort
from app.domain.enums import Priority, RequestType
from app.domain.state import AgentState, Classification
from app.graph.context import NodeContext
from app.graph.nodes import classify_node, make_route_after_classify

from tests.unit.fakes import StubLlm, make_ctx


def _state(subject: str = "", body: str = "hello") -> AgentState:
    return AgentState(request_id="r1", channel="email", raw_subject=subject, raw_body=body)


async def test_classify_billing_high_confidence(sandbox_ctx: NodeContext) -> None:
    state = _state(body="I need a refund for my invoice, this is urgent")
    delta = await classify_node(state, sandbox_ctx)

    classification = delta["classification"]
    assert isinstance(classification, Classification)
    assert classification.request_type is RequestType.BILLING
    assert classification.priority is Priority.URGENT
    assert classification.confidence > 0.5


async def test_classify_unknown_gets_low_confidence(sandbox_ctx: NodeContext) -> None:
    state = _state(body="just saying hello, nothing in particular")
    delta = await classify_node(state, sandbox_ctx)
    assert delta["classification"].confidence < 0.5


async def test_classify_handles_unparseable_output() -> None:
    # An LLM that returns prose instead of JSON must degrade to review, not crash.
    ctx = make_ctx(llm=StubLlm(reply="I think this is probably billing?"))
    delta = await classify_node(_state(body="anything"), ctx)
    classification = delta["classification"]
    assert classification.confidence == 0.0
    assert classification.request_type is RequestType.OTHER


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [(0.9, "extract"), (0.5, "extract"), (0.49, "needs_review"), (0.0, "needs_review")],
)
def test_route_after_classify(confidence: float, expected: str) -> None:
    route = make_route_after_classify(0.5)
    state = _state()
    state.classification = Classification(
        request_type=RequestType.OTHER, priority=Priority.LOW, confidence=confidence
    )
    assert route(state) == expected


def test_route_after_classify_none_goes_to_review() -> None:
    assert make_route_after_classify(0.5)(_state()) == "needs_review"


def test_llm_port_is_structural() -> None:
    # Sanity: our stub satisfies the port without inheritance.
    assert isinstance(StubLlm(reply="x"), LlmPort)
