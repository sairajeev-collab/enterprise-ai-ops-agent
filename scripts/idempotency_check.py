"""Static-ish guard: prove every external idempotency key is deterministic.

The failure this prevents: run the worker twice during a deploy (or replay a job),
and if any adapter's idempotency key contains a timestamp or a fresh UUID, you get
duplicate Jira tickets / duplicate emails. The whole safety story rests on those
keys being a pure function of ``request_id``.

So this drives the pipeline twice with the *same* request id, captures the
idempotency key handed to every side-effecting adapter, and asserts the two runs
produced the identical set of keys. If a key had any run-to-run entropy, the sets
differ and this exits non-zero. Wired into ``make idempotency-check`` and CI.

Run: python -m scripts.idempotency_check
"""

from __future__ import annotations

import asyncio
import sys

from app.adapters.base import (
    EmailMessage,
    EmailPort,
    EmailReceipt,
    NotifierPort,
    NotifyMessage,
    TicketPort,
    TicketRef,
    TicketRequest,
)
from app.adapters.knowledge.sandbox import SandboxKnowledge
from app.adapters.llm.sandbox import SandboxLlm
from app.domain.state import AgentState
from app.graph.build import Pipeline
from app.graph.context import NodeConfig, NodeContext

_FIXED_REQUEST_ID = "idempotency-check-fixed-id"
_BODY = "I need a refund for my invoice urgently. from Jane Smith jane@acme.com"


class _CaptureTickets(TicketPort):
    def __init__(self) -> None:
        self.keys: list[str] = []

    async def create_ticket(self, ticket: TicketRequest, *, idempotency_key: str) -> TicketRef:
        self.keys.append(idempotency_key)
        return TicketRef(key="OPS-1", url="https://sandbox.local/browse/OPS-1")


class _CaptureEmail(EmailPort):
    def __init__(self) -> None:
        self.keys: list[str] = []

    async def send(self, message: EmailMessage, *, idempotency_key: str) -> EmailReceipt:
        self.keys.append(idempotency_key)
        return EmailReceipt(message_id=f"<{idempotency_key}@check>")


class _CaptureNotifier(NotifierPort):
    def __init__(self) -> None:
        self.keys: list[str] = []

    async def notify(self, message: NotifyMessage, *, idempotency_key: str) -> None:
        self.keys.append(idempotency_key)


async def _run_once() -> dict[str, list[str]]:
    tickets, email, notifier = _CaptureTickets(), _CaptureEmail(), _CaptureNotifier()
    ctx = NodeContext(
        llm=SandboxLlm(),
        knowledge=SandboxKnowledge(),
        tickets=tickets,
        email=email,
        notifier=notifier,
        config=NodeConfig(),
    )
    state = AgentState(
        request_id=_FIXED_REQUEST_ID, channel="email", raw_subject="Refund", raw_body=_BODY
    )
    await Pipeline(ctx).run(state)
    return {"ticket": tickets.keys, "email": email.keys, "notify": notifier.keys}


async def _main() -> int:
    first = await _run_once()
    second = await _run_once()

    print("Idempotency key determinism check")
    print("=================================")
    ok = True
    for surface in ("ticket", "email", "notify"):
        same = first[surface] == second[surface]
        ok = ok and same
        mark = "ok " if same else "FAIL"
        print(f"  [{mark}] {surface:<8} run1={first[surface]}  run2={second[surface]}")

    if not ok:
        print("\nFAIL: an idempotency key changed between identical runs (non-deterministic).")
        return 1
    print("\nAll external idempotency keys are a pure function of request_id.")
    return 0


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
