"""Idempotency: replaying a request must not double-create external effects.

The bug this pins: run the worker twice during a deploy and create N duplicate
Jira tickets. The guard is deterministic idempotency keys derived from request_id,
honored by every side-effecting adapter (ADR-0017).
"""

from __future__ import annotations

import pytest
from app.adapters.email.sandbox import SandboxEmail
from app.adapters.jira.sandbox import SandboxTickets
from app.adapters.knowledge.sandbox import SandboxKnowledge
from app.adapters.llm.sandbox import SandboxLlm
from app.adapters.slack.sandbox import SandboxNotifier
from app.domain.state import AgentState
from app.graph.build import Pipeline
from app.graph.context import NodeConfig, NodeContext

pytestmark = pytest.mark.integration

_BODY = "I need a refund for my invoice urgently. from Jane Smith jane@acme.com"


async def test_replaying_one_request_creates_exactly_one_of_each() -> None:
    # Shared, deduping adapters across every replay — the real Jira/email/Slack
    # adapters dedupe the same way (label search, Message-ID, keyed post).
    tickets = SandboxTickets()
    email = SandboxEmail()
    notifier = SandboxNotifier()
    ctx = NodeContext(
        llm=SandboxLlm(),
        knowledge=SandboxKnowledge(),
        tickets=tickets,
        email=email,
        notifier=notifier,
        config=NodeConfig(),
    )
    pipeline = Pipeline(ctx)

    def fresh_state() -> AgentState:
        # Same request_id every time -> same idempotency keys.
        return AgentState(
            request_id="dup-guard-1", channel="email", raw_subject="Refund", raw_body=_BODY
        )

    for _ in range(10):
        plan = await pipeline.run(fresh_state())
        assert plan.ticket is not None  # sanity: the run actually did the work

    assert len(tickets.created) == 1, "10 replays must yield exactly one ticket"
    assert len(email.outbox) == 1, "exactly one email"
    assert len(notifier.sent) == 1, "exactly one Slack post"


async def test_different_requests_are_not_deduped() -> None:
    # Guard against the opposite bug: keys must be unique *per request*, or two
    # real customers would collapse into one ticket.
    tickets = SandboxTickets()
    ctx = NodeContext(
        llm=SandboxLlm(),
        knowledge=SandboxKnowledge(),
        tickets=tickets,
        email=SandboxEmail(),
        notifier=SandboxNotifier(),
        config=NodeConfig(),
    )
    pipeline = Pipeline(ctx)

    for request_id in ("cust-a", "cust-b", "cust-c"):
        await pipeline.run(
            AgentState(request_id=request_id, channel="email", raw_subject="Refund", raw_body=_BODY)
        )

    assert len(tickets.created) == 3
