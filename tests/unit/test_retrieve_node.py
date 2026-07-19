"""Unit tests for the knowledge retrieval node."""

from __future__ import annotations

from app.adapters.base import KnowledgeHit
from app.domain.enums import Priority, RequestType
from app.domain.state import AgentState, Classification, Extracted
from app.graph.context import NodeContext
from app.graph.nodes import retrieve_node

from tests.unit.fakes import StaticKnowledge, make_ctx


def _ready_state(summary: str) -> AgentState:
    state = AgentState(request_id="r1", channel="email", raw_body="body")
    state.classification = Classification(
        request_type=RequestType.BILLING, priority=Priority.MEDIUM, confidence=0.9
    )
    state.extracted = Extracted(subject="refund", summary=summary)
    return state


async def test_retrieve_returns_relevant_seed_docs(sandbox_ctx: NodeContext) -> None:
    delta = await retrieve_node(_ready_state("I want a refund for my invoice"), sandbox_ctx)
    hits = delta["knowledge"]
    assert hits, "expected at least one seed doc to match"
    assert any("refund" in hit.text.lower() for hit in hits)
    assert all(0.0 <= hit.score <= 1.0 for hit in hits)


async def test_retrieve_respects_top_k() -> None:
    many = [KnowledgeHit(id=f"d{i}", text="t", score=0.5) for i in range(10)]
    ctx = make_ctx(knowledge=StaticKnowledge(many))
    delta = await retrieve_node(_ready_state("anything"), ctx)
    assert len(delta["knowledge"]) <= ctx.config.knowledge_top_k
