"""Unit tests for the extract node."""

from __future__ import annotations

from app.domain.state import AgentState, Extracted
from app.graph.context import NodeContext
from app.graph.nodes import extract_node

from tests.unit.fakes import StubLlm, make_ctx


def _state(body: str) -> AgentState:
    return AgentState(request_id="r1", channel="email", raw_subject="Subject line", raw_body=body)


async def test_extract_pulls_email_and_summary(sandbox_ctx: NodeContext) -> None:
    body = "Hello, my order is broken. Please help. from John Smith john@acme.com"
    delta = await extract_node(_state(body), sandbox_ctx)
    extracted = delta["extracted"]
    assert isinstance(extracted, Extracted)
    assert extracted.customer_email == "john@acme.com"
    assert extracted.summary


async def test_extract_falls_back_on_bad_json() -> None:
    ctx = make_ctx(llm=StubLlm(reply="not json at all"))
    delta = await extract_node(_state("body text here"), ctx)
    extracted = delta["extracted"]
    # Graceful degradation to raw inputs rather than an exception.
    assert extracted.subject == "Subject line"
    assert extracted.summary == "body text here"
