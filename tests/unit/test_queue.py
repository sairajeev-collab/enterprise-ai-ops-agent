"""Reliable-queue semantics: claim/ack, crash redelivery, and dead-lettering."""

from __future__ import annotations

import time

import fakeredis.aioredis
from app.jobs.queue import JobQueue


def _queue(*, visibility: int = 300, max_redeliveries: int = 2) -> JobQueue:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return JobQueue(
        redis,
        key="test:jobs",
        visibility_timeout_seconds=visibility,
        max_redeliveries=max_redeliveries,
    )


async def test_claim_moves_job_to_processing() -> None:
    q = _queue()
    await q.enqueue("r1")
    assert await q.depth() == 1

    claimed = await q.claim(timeout_seconds=1)
    assert claimed == "r1"
    # No longer pending, but held in-flight until acked.
    assert await q.depth() == 0
    assert await q.processing_depth() == 1

    await q.ack("r1")
    assert await q.processing_depth() == 0


async def test_claim_returns_none_when_empty() -> None:
    assert await _queue().claim(timeout_seconds=1) is None


async def test_reap_redelivers_abandoned_job() -> None:
    # Zero visibility: a claimed-but-unacked job is immediately eligible.
    q = _queue(visibility=0)
    await q.enqueue("r1")
    await q.claim(timeout_seconds=1)  # claimed, never acked (worker "crashed")

    redelivered, dead = await q.reap()
    assert (redelivered, dead) == (1, 0)
    assert await q.depth() == 1  # back on the pending queue
    assert await q.processing_depth() == 0


async def test_reap_dead_letters_after_max_redeliveries() -> None:
    q = _queue(visibility=0, max_redeliveries=2)
    await q.enqueue("r1")

    # Each cycle: claim then reap (simulating repeated crashes).
    for _ in range(3):
        await q.claim(timeout_seconds=1)
        await q.reap()

    assert await q.depth() == 0
    assert await q.processing_depth() == 0
    assert await q.dead_letter_depth() == 1


async def test_ack_is_final() -> None:
    q = _queue(visibility=0)
    await q.enqueue("r1")
    await q.claim(timeout_seconds=1)
    await q.ack("r1")

    # An acked job is gone; reaping finds nothing to redeliver.
    assert await q.reap() == (0, 0)
    assert await q.depth() == 0


async def test_stuck_jobs_flags_aged_in_flight() -> None:
    q = _queue(visibility=300)
    await q.enqueue("r1")
    await q.claim(timeout_seconds=1)

    # Freshly claimed -> not stuck.
    assert await q.stuck_jobs(older_than_seconds=300) == []

    # Age the heartbeat past the threshold.
    await q._redis.hset(q._heartbeats, "r1", str(time.time() - 1000))
    stuck = await q.stuck_jobs(older_than_seconds=300)
    assert len(stuck) == 1
    assert stuck[0][0] == "r1"
    assert stuck[0][1] >= 1000
