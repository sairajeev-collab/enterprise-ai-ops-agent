"""In-memory sandbox ticket store.

Mirrors the real adapter's idempotency contract: creating a ticket with an
already-seen ``idempotency_key`` returns the same :class:`TicketRef` instead of a
new one. Useful for asserting the pipeline never double-creates on replay.
"""

from __future__ import annotations

from app.adapters.base import TicketPort, TicketRef, TicketRequest


class SandboxTickets(TicketPort):
    def __init__(self, *, project_key: str = "OPS") -> None:
        self._project_key = project_key
        self._counter = 0
        self._by_key: dict[str, TicketRef] = {}
        # Exposed for tests/inspection.
        self.created: list[TicketRequest] = []

    async def create_ticket(self, ticket: TicketRequest, *, idempotency_key: str) -> TicketRef:
        if idempotency_key in self._by_key:
            return self._by_key[idempotency_key]

        self._counter += 1
        key = f"{self._project_key}-{self._counter}"
        ref = TicketRef(key=key, url=f"https://sandbox.local/browse/{key}")
        self._by_key[idempotency_key] = ref
        self.created.append(ticket)
        return ref
