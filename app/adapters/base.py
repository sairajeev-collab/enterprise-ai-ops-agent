"""Port definitions, data-transfer objects, and the adapter exception hierarchy.

Ports are ``typing.Protocol`` classes describing the *minimum* an adapter must
provide. Domain and graph code depend only on these, never on a concrete vendor
client. DTOs are Pydantic models so boundary data is validated in both
directions.

Exception contract
------------------
Adapters translate vendor-specific failures into exactly one of:

* :class:`TransientAdapterError`. Retrying may succeed (timeouts, 5xx, 429).
* :class:`PermanentAdapterError`. Retrying will not help (4xx, bad config).

The retry layer (:mod:`app.graph.retry`) keys off these types, so it never has to
understand vendor error codes.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class AdapterError(Exception):
    """Base class for all adapter failures."""

    code: str = "adapter_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class TransientAdapterError(AdapterError):
    """A retryable failure (network blip, timeout, upstream 5xx/429)."""

    code = "transient_adapter_error"


class PermanentAdapterError(AdapterError):
    """A non-retryable failure (invalid input, auth, upstream 4xx)."""

    code = "permanent_adapter_error"


# --------------------------------------------------------------------------- #
# DTOs
# --------------------------------------------------------------------------- #
class KnowledgeDoc(BaseModel):
    """A knowledge-base chunk to index."""

    id: str
    text: str = Field(min_length=1)
    source: str = ""
    metadata: dict[str, str] = Field(default_factory=dict)


class KnowledgeHit(BaseModel):
    """A retrieval result with its relevance score in [0, 1]."""

    id: str
    text: str
    score: float
    source: str = ""


class TicketRequest(BaseModel):
    summary: str = Field(min_length=1, max_length=255)
    description: str
    issue_type: str = "Task"
    priority: str = "Medium"
    labels: list[str] = Field(default_factory=list)


class TicketRef(BaseModel):
    """Reference to a created ticket. ``key`` is stable and idempotent-safe."""

    key: str
    url: str


class EmailMessage(BaseModel):
    to: str = Field(min_length=3)
    subject: str = Field(min_length=1, max_length=255)
    body: str


class EmailReceipt(BaseModel):
    message_id: str


class NotifyMessage(BaseModel):
    text: str = Field(min_length=1)
    channel: str = ""


# --------------------------------------------------------------------------- #
# Ports
# --------------------------------------------------------------------------- #
@runtime_checkable
class LlmPort(Protocol):
    """Text generation and embedding over an OpenAI-compatible surface."""

    async def complete(
        self, *, system: str, user: str, temperature: float = 0.0, json_mode: bool = False
    ) -> str:
        """Return a single completion for the given system/user prompt.

        When ``json_mode`` is set, the adapter requests a strict JSON object from
        the model (OpenAI-compatible ``response_format``), which the classify and
        extract nodes rely on for reliable parsing.
        """
        ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text, order preserved."""
        ...


@runtime_checkable
class KnowledgePort(Protocol):
    """Semantic search over the company knowledge base."""

    async def search(self, query: str, *, top_k: int = 5) -> list[KnowledgeHit]: ...

    async def upsert(self, docs: list[KnowledgeDoc]) -> int:
        """Index or update documents; returns the count written."""
        ...

    async def ensure_ready(self) -> None:
        """Idempotently create/verify backing storage (collections, indexes)."""
        ...


@runtime_checkable
class TicketPort(Protocol):
    """Issue tracker integration (Jira and equivalents)."""

    async def create_ticket(self, ticket: TicketRequest, *, idempotency_key: str) -> TicketRef: ...


@runtime_checkable
class EmailPort(Protocol):
    """Outbound email."""

    async def send(self, message: EmailMessage, *, idempotency_key: str) -> EmailReceipt: ...


@runtime_checkable
class NotifierPort(Protocol):
    """Team chat notifications (Slack and equivalents)."""

    async def notify(self, message: NotifyMessage, *, idempotency_key: str) -> None: ...
