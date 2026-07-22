"""End-to-end: a poisoned draft is held, not emailed (ADR-0018).

The unit tests prove the guardrail's logic; this proves the *wiring*, that the
reply node actually consults the guardrail and suppresses the email send on a
violation, while a clean draft still goes out.
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

# A message that classifies cleanly (keyword "refund") and carries an email, so
# the pipeline reaches the reply node and would send if the gate let it.
_BODY = "I need a refund for my invoice urgently. Reach me at jane@acme.com — Jane Doe"


class _PoisonReplyLlm(SandboxLlm):
    """Sandbox LLM that draws a leaky reply but is otherwise deterministic."""

    async def complete(
        self, *, system: str, user: str, temperature: float = 0.0, json_mode: bool = False
    ) -> str:
        if self._task_of(system) == "draft_reply":
            return "Hi Jane, please contact insider@rival.com about your refund."
        return await super().complete(
            system=system, user=user, temperature=temperature, json_mode=json_mode
        )


def _context(llm: SandboxLlm, email: SandboxEmail) -> NodeContext:
    return NodeContext(
        llm=llm,
        knowledge=SandboxKnowledge(),
        tickets=SandboxTickets(),
        email=email,
        notifier=SandboxNotifier(),
        config=NodeConfig(),
    )


def _state() -> AgentState:
    return AgentState(
        request_id="guardrail-e2e", channel="email", raw_subject="Refund", raw_body=_BODY
    )


async def test_poisoned_reply_is_held_not_sent() -> None:
    email = SandboxEmail()
    plan = await Pipeline(_context(_PoisonReplyLlm(), email)).run(_state())

    assert plan.reply is not None
    assert plan.reply.sent is False, "a leaky draft must not be emailed"
    assert plan.reply.guardrail_violations, "the hold reason must be recorded"
    assert email.outbox == [], "no email should have left the building"
    # The rest of the pipeline still completes (ticket opened, run recorded).
    assert plan.ticket is not None


async def test_clean_reply_still_sends() -> None:
    email = SandboxEmail()
    plan = await Pipeline(_context(SandboxLlm(), email)).run(_state())

    assert plan.reply is not None
    assert plan.reply.sent is True
    assert plan.reply.guardrail_violations == []
    assert len(email.outbox) == 1
