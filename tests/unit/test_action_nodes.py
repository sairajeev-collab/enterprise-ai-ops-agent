"""Unit tests for the side-effecting nodes: ticket, reply, notify, persist, report."""

from __future__ import annotations

from app.adapters.base import KnowledgeHit
from app.adapters.slack.sandbox import SandboxNotifier
from app.domain.enums import Priority, RequestType
from app.domain.state import AgentState, Classification, Extracted, Reply
from app.graph.nodes import (
    create_ticket_node,
    notify_node,
    persist_node,
    reply_node,
)

from tests.unit.fakes import RecordingEmail, RecordingTickets, make_ctx


def _state(*, email: str = "") -> AgentState:
    state = AgentState(request_id="req-1", channel="email", raw_subject="Broken", raw_body="help")
    state.classification = Classification(
        request_type=RequestType.TECHNICAL_SUPPORT, priority=Priority.HIGH, confidence=0.9
    )
    state.extracted = Extracted(customer_name="Jane Doe", customer_email=email, subject="Broken")
    state.knowledge = [KnowledgeHit(id="k1", text="check status page", score=0.7, source="rb")]
    return state


async def test_create_ticket_uses_request_scoped_idempotency_key() -> None:
    tickets = RecordingTickets()
    ctx = make_ctx(tickets=tickets)
    delta = await create_ticket_node(_state(), ctx)

    assert delta["ticket"].key == "OPS-99"
    request, key = tickets.requests[0]
    assert key == "req-req-1"
    assert request.priority == "High"  # mapped from domain HIGH
    assert "technical_support" in request.labels


async def test_create_ticket_is_idempotent_on_replay() -> None:
    ctx = make_ctx()  # sandbox tickets dedupe by key
    first = await create_ticket_node(_state(), ctx)
    second = await create_ticket_node(_state(), ctx)
    assert first["ticket"].key == second["ticket"].key


async def test_reply_sends_email_when_address_present() -> None:
    email = RecordingEmail()
    ctx = make_ctx(email=email)
    state = _state(email="jane@acme.com")
    state.ticket = (await create_ticket_node(state, ctx))["ticket"]

    delta = await reply_node(state, ctx)
    reply = delta["reply"]
    assert isinstance(reply, Reply)
    assert reply.sent is True
    assert reply.message_id is not None
    assert email.sent[0].to == "jane@acme.com"


async def test_reply_skips_send_when_no_email() -> None:
    email = RecordingEmail()
    ctx = make_ctx(email=email)
    state = _state(email="")
    state.ticket = (await create_ticket_node(state, ctx))["ticket"]

    delta = await reply_node(state, ctx)
    assert delta["reply"].sent is False
    assert email.sent == []


async def test_notify_posts_message() -> None:
    notifier = SandboxNotifier()
    ctx = make_ctx(notifier=notifier)
    state = _state()
    state.ticket = (await create_ticket_node(state, ctx))["ticket"]

    delta = await notify_node(state, ctx)
    assert delta["notification_sent"] is True
    assert notifier.sent
    assert state.ticket.key in notifier.sent[0].text


async def test_persist_builds_summary_record() -> None:
    ctx = make_ctx()
    state = _state(email="jane@acme.com")
    state.ticket = (await create_ticket_node(state, ctx))["ticket"]
    state.notification_sent = True

    delta = await persist_node(state, ctx)
    record = delta["summary_record"]
    assert record["request_id"] == "req-1"
    assert record["ticket_key"] == state.ticket.key
    assert record["request_type"] == "technical_support"
    assert record["knowledge_hits"] == ["k1"]
