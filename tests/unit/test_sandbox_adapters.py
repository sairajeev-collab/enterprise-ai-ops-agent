"""Contract tests for the sandbox adapters.

These pin the behavior the real adapters must also honor: idempotency by key and
deterministic outputs. Keeping them here guards against the sandbox drifting from
the port contract.
"""

from __future__ import annotations

from app.adapters.base import (
    EmailMessage,
    KnowledgeDoc,
    NotifyMessage,
    TicketRequest,
)
from app.adapters.email.sandbox import SandboxEmail
from app.adapters.jira.sandbox import SandboxTickets
from app.adapters.knowledge.sandbox import SandboxKnowledge
from app.adapters.llm.sandbox import SandboxLlm
from app.adapters.slack.sandbox import SandboxNotifier


async def test_tickets_idempotent_by_key() -> None:
    tickets = SandboxTickets()
    req = TicketRequest(summary="s", description="d")
    a = await tickets.create_ticket(req, idempotency_key="k1")
    b = await tickets.create_ticket(req, idempotency_key="k1")
    c = await tickets.create_ticket(req, idempotency_key="k2")
    assert a.key == b.key
    assert a.key != c.key
    assert len(tickets.created) == 2  # k1 once, k2 once


async def test_email_idempotent_and_captured() -> None:
    email = SandboxEmail()
    msg = EmailMessage(to="a@b.com", subject="hi", body="body")
    r1 = await email.send(msg, idempotency_key="e1")
    r2 = await email.send(msg, idempotency_key="e1")
    assert r1.message_id == r2.message_id
    assert len(email.outbox) == 1


async def test_notifier_dedupes_by_key() -> None:
    notifier = SandboxNotifier()
    await notifier.notify(NotifyMessage(text="hello"), idempotency_key="n1")
    await notifier.notify(NotifyMessage(text="hello"), idempotency_key="n1")
    assert len(notifier.sent) == 1


async def test_knowledge_search_and_upsert() -> None:
    kb = SandboxKnowledge()
    hits = await kb.search("refund policy for invoice")
    assert hits and any("refund" in h.text.lower() for h in hits)

    added = await kb.upsert([KnowledgeDoc(id="new", text="widgets ship on tuesdays")])
    assert added == 1
    new_hits = await kb.search("when do widgets ship")
    assert any(h.id == "new" for h in new_hits)


async def test_knowledge_empty_query_returns_nothing() -> None:
    kb = SandboxKnowledge()
    assert await kb.search("   ") == []


async def test_llm_classify_is_deterministic() -> None:
    llm = SandboxLlm()
    system = "TASK: classify\n..."
    out1 = await llm.complete(system=system, user="I need a refund urgently")
    out2 = await llm.complete(system=system, user="I need a refund urgently")
    assert out1 == out2
    assert '"billing"' in out1


async def test_llm_embed_is_stable_and_sized() -> None:
    llm = SandboxLlm()
    v1 = await llm.embed(["hello"])
    v2 = await llm.embed(["hello"])
    assert v1 == v2
    assert len(v1) == 1 and len(v1[0]) == 16
