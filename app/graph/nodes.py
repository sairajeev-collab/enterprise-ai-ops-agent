"""Pipeline nodes.

Every node is an ``async`` function ``(state, ctx) -> dict`` that returns only the
fields it changed. Nodes reach external systems exclusively through ``ctx`` ports,
wrapped in :func:`retry_async`, and never touch the database. Persistence is the
worker's responsibility (ADR-0003/0004). This keeps each node independently
unit-testable with sandbox adapters and no infrastructure.

The LLM prompts carry a ``TASK:`` marker on the first line. Real models treat it
as an ordinary instruction; the sandbox model reads it to stay deterministic.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

from app.adapters.base import (
    EmailMessage,
    NotifyMessage,
    PermanentAdapterError,
    TicketRequest,
)
from app.domain.enums import (
    JIRA_PRIORITY_BY_DOMAIN,
    Priority,
    RequestType,
    RunStatus,
)
from app.domain.state import AgentState, Classification, Extracted, Reply
from app.graph.context import NodeContext
from app.graph.retry import retry_async
from app.guardrails import check_reply
from app.logging import get_logger
from app.metrics import REPLY_GUARDRAIL_BLOCKED

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _parse_json_object(text: str) -> dict[str, Any]:
    """Parse the first JSON object found in ``text``.

    Models sometimes wrap JSON in prose or code fences; we extract the outermost
    brace-delimited span. Raises ``ValueError`` if no valid object is present.
    """

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in model output")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("model output was not a JSON object")
    return cast("dict[str, Any]", parsed)


async def _retry(ctx: NodeContext, op_name: str, operation: Any) -> Any:
    return await retry_async(
        operation,
        attempts=ctx.config.max_attempts,
        base_delay=ctx.config.base_delay_seconds,
        op_name=op_name,
    )


# --------------------------------------------------------------------------- #
# Nodes
# --------------------------------------------------------------------------- #
async def classify_node(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    system = (
        "TASK: classify\n"
        "You are a support operations classifier. Classify the customer message "
        "into request_type (one of: billing, technical_support, account, sales, "
        "complaint, other) and priority (one of: low, medium, high, urgent). "
        'Respond ONLY with JSON: {"request_type": "...", "priority": "...", '
        '"confidence": 0.0, "reason": "..."}.'
    )
    user = f"{state.raw_subject}\n\n{state.raw_body}".strip()

    raw = await _retry(
        ctx, "llm.classify", lambda: ctx.llm.complete(system=system, user=user, json_mode=True)
    )

    try:
        data = _parse_json_object(raw)
        classification = Classification(
            request_type=RequestType(str(data["request_type"]).lower()),
            priority=Priority(str(data["priority"]).lower()),
            confidence=float(data["confidence"]),
            reason=str(data.get("reason", "")),
        )
    except (ValueError, KeyError, TypeError) as exc:
        # Unparseable/invalid output is not a crash: emit a zero-confidence result
        # so the graph routes to human review rather than acting blindly.
        logger.warning("classify_parse_failed", extra={"detail": str(exc)})
        classification = Classification(
            request_type=RequestType.OTHER,
            priority=Priority.MEDIUM,
            confidence=0.0,
            reason="classifier output could not be parsed",
        )

    return {"classification": classification}


async def extract_node(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    system = (
        "TASK: extract\n"
        "Extract structured fields from the customer message. Respond ONLY with "
        'JSON: {"customer_name": "...", "customer_email": "...", "subject": "...", '
        '"summary": "...", "entities": {}}.'
    )
    raw = await _retry(
        ctx,
        "llm.extract",
        lambda: ctx.llm.complete(system=system, user=state.raw_body, json_mode=True),
    )

    try:
        data = _parse_json_object(raw)
        extracted = Extracted(
            customer_name=str(data.get("customer_name", "")),
            customer_email=str(data.get("customer_email", "")),
            subject=str(data.get("subject", "") or state.raw_subject),
            summary=str(data.get("summary", "") or state.raw_body[:280]),
            entities=dict(data.get("entities", {}) or {}),
        )
    except (ValueError, TypeError) as exc:
        # Degrade gracefully: fall back to the raw inputs rather than failing.
        logger.warning("extract_parse_failed", extra={"detail": str(exc)})
        extracted = Extracted(
            subject=state.raw_subject,
            summary=state.raw_body[:280],
        )

    return {"extracted": extracted}


async def retrieve_node(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    classification = _require(state.classification, "classify")
    extracted = _require(state.extracted, "extract")
    query = f"{classification.request_type.value} {extracted.subject} {extracted.summary}".strip()

    hits = await _retry(
        ctx,
        "knowledge.search",
        lambda: ctx.knowledge.search(query, top_k=ctx.config.knowledge_top_k),
    )
    return {"knowledge": hits}


async def create_ticket_node(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    classification = _require(state.classification, "classify")
    extracted = _require(state.extracted, "extract")

    knowledge_block = "\n".join(f"- {hit.text} (source: {hit.source})" for hit in state.knowledge)
    description = (
        f"{state.raw_body}\n\n"
        f"Classification: {classification.request_type.value} "
        f"(priority {classification.priority.value}, "
        f"confidence {classification.confidence:.2f})\n"
        f"Reason: {classification.reason}\n\n"
        f"Relevant knowledge:\n{knowledge_block or '- none found'}"
    )
    summary = (extracted.subject or state.raw_subject or state.raw_body[:80]).strip()[:255]

    ticket_request = TicketRequest(
        summary=summary or "Customer request",
        description=description,
        issue_type="Task",
        priority=JIRA_PRIORITY_BY_DOMAIN[classification.priority],
        labels=[classification.request_type.value],
    )
    idempotency_key = f"req-{state.request_id}"

    ref = await _retry(
        ctx,
        "tickets.create",
        lambda: ctx.tickets.create_ticket(ticket_request, idempotency_key=idempotency_key),
    )
    return {"ticket": ref}


async def reply_node(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    extracted = _require(state.extracted, "extract")
    ticket = _require(state.ticket, "create_ticket")
    top_knowledge = state.knowledge[0].text if state.knowledge else ""

    system = (
        "TASK: draft_reply\n"
        "Draft a concise, professional customer reply. Acknowledge the issue, "
        "reference that a ticket has been opened, and set expectations. Do not "
        "invent facts beyond the provided context."
    )
    user = (
        f"customer_name: {extracted.customer_name}\n"
        f"summary: {extracted.summary}\n"
        f"ticket: {ticket.key}\n"
        f"knowledge: {top_knowledge}"
    )
    body = await _retry(ctx, "llm.reply", lambda: ctx.llm.complete(system=system, user=user))

    # Gate the draft before it leaves the building (ADR-0018). A failed gate holds
    # the email and flags the run for a human rather than sending questionable text.
    gate = check_reply(body, customer_email=extracted.customer_email)

    sent = False
    message_id: str | None = None
    if extracted.customer_email and gate.ok:
        message = EmailMessage(
            to=extracted.customer_email,
            subject=f"Re: {extracted.subject or 'your request'}",
            body=body,
        )
        receipt = await _retry(
            ctx,
            "email.send",
            lambda: ctx.email.send(message, idempotency_key=f"req-{state.request_id}-reply"),
        )
        sent = True
        message_id = receipt.message_id
    elif not gate.ok:
        REPLY_GUARDRAIL_BLOCKED.inc()
        logger.warning(
            "reply_guardrail_blocked",
            extra={"request_id": state.request_id, "detail": gate.reason},
        )

    return {
        "reply": Reply(
            body=body, sent=sent, message_id=message_id, guardrail_violations=gate.violations
        )
    }


async def notify_node(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    classification = _require(state.classification, "classify")
    extracted = _require(state.extracted, "extract")
    ticket = _require(state.ticket, "create_ticket")

    text = (
        f"New {classification.request_type.value} request "
        f"(priority {classification.priority.value}) "
        f"from {extracted.customer_name or 'unknown'}.\n"
        f"Ticket: {ticket.key} {ticket.url}\n"
        f"Summary: {extracted.summary}"
    )
    message = NotifyMessage(text=text, channel=ctx.config.slack_channel)

    await _retry(
        ctx,
        "notifier.notify",
        lambda: ctx.notifier.notify(message, idempotency_key=f"req-{state.request_id}-notify"),
    )
    return {"notification_sent": True}


async def persist_node(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    """Assemble the canonical record for this run.

    Pure: it does not write to the database. It produces the structured summary
    the worker will commit transactionally (single, atomic DB write) and that the
    report node summarizes.
    """

    classification = _require(state.classification, "classify")
    extracted = _require(state.extracted, "extract")
    record: dict[str, Any] = {
        "request_id": state.request_id,
        "channel": state.channel.value,
        "request_type": classification.request_type.value,
        "priority": classification.priority.value,
        "confidence": classification.confidence,
        "customer_name": extracted.customer_name,
        "customer_email": extracted.customer_email,
        "subject": extracted.subject,
        "ticket_key": state.ticket.key if state.ticket else None,
        "ticket_url": state.ticket.url if state.ticket else None,
        "reply_sent": bool(state.reply and state.reply.sent),
        "reply_held": bool(state.reply and state.reply.guardrail_violations),
        "notified": state.notification_sent,
        "knowledge_hits": [hit.id for hit in state.knowledge],
    }
    return {"summary_record": record}


async def report_node(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    record = state.summary_record or {}
    system = (
        "TASK: report\n"
        "Write a brief manager-facing summary of how this request was handled. "
        "Be factual and concise."
    )
    report = await _retry(
        ctx,
        "llm.report",
        lambda: ctx.llm.complete(system=system, user=json.dumps(record, default=str)),
    )
    return {"report": report, "status": RunStatus.COMPLETED}


async def needs_review_node(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    reason = "classification confidence below threshold; routed to human review"
    if state.classification:
        reason = (
            f"low confidence ({state.classification.confidence:.2f}) for "
            f"{state.classification.request_type.value}: {state.classification.reason}"
        )
    logger.info("routed_to_review", extra={"request_id": state.request_id})
    return {"status": RunStatus.NEEDS_REVIEW, "review_reason": reason}


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #
def make_route_after_classify(threshold: float) -> Callable[[AgentState], str]:
    """Build the conditional edge, closing over the confidence threshold.

    LangGraph conditional edges receive only the state, so the builder binds the
    configured threshold here rather than the node reaching into a context.
    """

    def route_after_classify(state: AgentState) -> str:
        classification = state.classification
        if classification is None or classification.confidence < threshold:
            return "needs_review"
        return "extract"

    return route_after_classify


def _require(value: Any, produced_by: str) -> Any:
    if value is None:
        # Programmer error: nodes ran out of order. Fail loudly, do not paper over.
        raise PermanentAdapterError(
            f"pipeline invariant violated: expected output of '{produced_by}' node",
            code="pipeline_invariant",
        )
    return value
