"""Worker behavior: happy path, human-review routing, and failure/requeue."""

from __future__ import annotations

import pytest
from app.adapters.base import TransientAdapterError
from app.db.engine import session_scope
from app.db.repository import Repository
from app.deps import Container
from app.domain.enums import RunStatus
from app.jobs.worker import process_request

pytestmark = pytest.mark.integration


async def _new_request(container: Container, body: str, subject: str = "s") -> str:
    async with session_scope(container.session_factory) as session:
        created = await Repository(session).create_request(
            channel="email", subject=subject, body=body
        )
        return created.id


async def _get(container: Container, request_id: str):
    async with session_scope(container.session_factory) as session:
        return await Repository(session).get_request(request_id)


async def test_worker_completes_and_records_steps(container: Container) -> None:
    rid = await _new_request(container, "I need a refund for my invoice. from Jo Lee jo@x.io")
    await process_request(container, rid)

    record = await _get(container, rid)
    assert record.status == RunStatus.COMPLETED.value
    # Every node checkpointed a step for auditability.
    async with session_scope(container.session_factory) as session:
        steps = await Repository(session).get_completed_steps(rid)
    assert "classify" in steps and "generate_report" in steps


async def test_worker_routes_low_confidence_to_review(container: Container) -> None:
    rid = await _new_request(container, "hello there friend, lovely weather today")
    await process_request(container, rid)

    record = await _get(container, rid)
    assert record.status == RunStatus.NEEDS_REVIEW.value
    assert any(a.kind == "review" for a in record.artifacts)
    assert all(a.kind != "ticket" for a in record.artifacts)


async def test_worker_requeues_on_transient_failure(container: Container) -> None:
    async def boom(*args: object, **kwargs: object) -> None:
        raise TransientAdapterError("jira down", code="stub")

    # Instance attribute shadows the sandbox method for this test.
    container.node_context.tickets.create_ticket = boom  # type: ignore[method-assign]

    rid = await _new_request(container, "I need a refund for my invoice. from Jo Lee jo@x.io")
    await process_request(container, rid)

    record = await _get(container, rid)
    assert record.status == RunStatus.QUEUED.value  # scheduled for retry
    assert record.attempts == 1
    assert await container.queue.depth() == 1  # re-enqueued


async def test_worker_fails_after_max_attempts(container: Container) -> None:
    async def boom(*args: object, **kwargs: object) -> None:
        raise TransientAdapterError("jira down", code="stub")

    container.node_context.tickets.create_ticket = boom  # type: ignore[method-assign]
    container.settings.max_attempts = 1  # exhaust immediately

    rid = await _new_request(container, "I need a refund for my invoice. from Jo Lee jo@x.io")
    await process_request(container, rid)

    record = await _get(container, rid)
    assert record.status == RunStatus.FAILED.value
    assert record.error is not None
    assert await container.queue.depth() == 0  # not requeued


async def test_worker_ignores_unknown_request(container: Container) -> None:
    # Must not raise on a missing id (at-least-once delivery can race deletes).
    await process_request(container, "no-such-id")
