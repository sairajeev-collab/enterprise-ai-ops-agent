"""Test doubles and helpers for node/unit tests.

These are deliberately small, hand-written fakes (not mocks): they implement the
ports structurally so tests exercise real code paths with controllable behavior.
"""

from __future__ import annotations

from app.adapters.base import (
    EmailMessage,
    EmailPort,
    EmailReceipt,
    KnowledgeHit,
    KnowledgePort,
    LlmPort,
    NotifierPort,
    NotifyMessage,
    TicketPort,
    TicketRef,
    TicketRequest,
    TransientAdapterError,
)
from app.adapters.email.sandbox import SandboxEmail
from app.adapters.jira.sandbox import SandboxTickets
from app.adapters.knowledge.sandbox import SandboxKnowledge
from app.adapters.llm.sandbox import SandboxLlm
from app.adapters.slack.sandbox import SandboxNotifier
from app.graph.context import NodeConfig, NodeContext


class StubLlm(LlmPort):
    """LLM that returns a fixed reply and a fixed-size embedding."""

    def __init__(self, *, reply: str = "", embed_dim: int = 8) -> None:
        self._reply = reply
        self._embed_dim = embed_dim
        self.calls: list[tuple[str, str]] = []

    async def complete(self, *, system: str, user: str, temperature: float = 0.0) -> str:
        self.calls.append((system, user))
        return self._reply

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self._embed_dim for _ in texts]


class FlakyLlm(LlmPort):
    """Fails with a transient error a set number of times, then succeeds."""

    def __init__(self, *, fail_times: int, reply: str = "ok") -> None:
        self._remaining = fail_times
        self._reply = reply
        self.attempts = 0

    async def complete(self, *, system: str, user: str, temperature: float = 0.0) -> str:
        self.attempts += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise TransientAdapterError("temporary", code="stub_transient")
        return self._reply

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


class RecordingNotifier(NotifierPort):
    def __init__(self) -> None:
        self.messages: list[NotifyMessage] = []

    async def notify(self, message: NotifyMessage, *, idempotency_key: str) -> None:
        self.messages.append(message)


class RecordingEmail(EmailPort):
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage, *, idempotency_key: str) -> EmailReceipt:
        self.sent.append(message)
        return EmailReceipt(message_id=f"<{idempotency_key}@test>")


class RecordingTickets(TicketPort):
    def __init__(self) -> None:
        self.requests: list[tuple[TicketRequest, str]] = []

    async def create_ticket(self, ticket: TicketRequest, *, idempotency_key: str) -> TicketRef:
        self.requests.append((ticket, idempotency_key))
        return TicketRef(key="OPS-99", url="https://test/browse/OPS-99")


class StaticKnowledge(KnowledgePort):
    def __init__(self, hits: list[KnowledgeHit]) -> None:
        self._hits = hits

    async def ensure_ready(self) -> None:
        return None

    async def upsert(self, docs: list) -> int:
        return len(docs)

    async def search(self, query: str, *, top_k: int = 5) -> list[KnowledgeHit]:
        return self._hits[:top_k]


def make_ctx(
    *,
    llm: LlmPort | None = None,
    knowledge: KnowledgePort | None = None,
    tickets: TicketPort | None = None,
    email: EmailPort | None = None,
    notifier: NotifierPort | None = None,
    config: NodeConfig | None = None,
) -> NodeContext:
    """Build a NodeContext from sandbox defaults, overriding any port."""

    return NodeContext(
        llm=llm or SandboxLlm(),
        knowledge=knowledge or SandboxKnowledge(),
        tickets=tickets or SandboxTickets(),
        email=email or SandboxEmail(),
        notifier=notifier or SandboxNotifier(),
        # Fast retries keep tests instant.
        config=config or NodeConfig(max_attempts=2, base_delay_seconds=0.0),
    )
