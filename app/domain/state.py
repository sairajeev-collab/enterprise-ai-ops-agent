"""The agent state passed through the LangGraph pipeline.

``AgentState`` is the single typed object every node reads from and contributes
to. Nodes return a partial dict of the fields they changed; LangGraph merges that
delta back in. Keeping this in one Pydantic model means the state is validated,
serializable (for checkpointing and status responses), and self-documenting.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.adapters.base import KnowledgeHit, TicketRef
from app.domain.enums import Channel, Priority, RequestType, RunStatus


class Classification(BaseModel):
    request_type: RequestType
    priority: Priority
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class Extracted(BaseModel):
    customer_name: str = ""
    customer_email: str = ""
    subject: str = ""
    summary: str = ""
    entities: dict[str, object] = Field(default_factory=dict)


class Reply(BaseModel):
    body: str
    sent: bool = False
    message_id: str | None = None
    # Populated when the output guardrail held the reply instead of sending it
    # (ADR-0018). Empty means the reply passed the gate.
    guardrail_violations: list[str] = Field(default_factory=list)


class AgentState(BaseModel):
    # --- inputs (set at intake) --- #
    request_id: str
    channel: Channel
    raw_subject: str = ""
    raw_body: str

    # --- produced by nodes --- #
    classification: Classification | None = None
    extracted: Extracted | None = None
    knowledge: list[KnowledgeHit] = Field(default_factory=list)
    ticket: TicketRef | None = None
    reply: Reply | None = None
    notification_sent: bool = False
    # Canonical record assembled by the persist node and committed by the worker.
    summary_record: dict[str, object] | None = None
    report: str | None = None

    # --- control --- #
    status: RunStatus = RunStatus.RUNNING
    review_reason: str | None = None
