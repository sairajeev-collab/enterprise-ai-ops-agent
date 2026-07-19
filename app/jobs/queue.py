"""Reliable Redis work queue.

A plain ``BLPOP`` loop is at-most-once: the moment a job is popped it is gone,
so a worker that crashes mid-run loses that job. This implementation is
at-least-once instead, using the well-known reliable-queue pattern:

* ``claim`` atomically moves a job id from the pending list to a ``processing``
  list (``BLMOVE``) and records a claim timestamp.
* ``ack`` removes the job from ``processing`` once the worker is done.
* If a worker dies between claim and ack, the job stays in ``processing``. The
  :meth:`reap` sweep (run periodically by the worker) redelivers any job whose
  claim is older than the visibility timeout.
* A job redelivered more than ``max_redeliveries`` times is parked on a
  dead-letter list for operator inspection rather than looping forever.

Combined with idempotent nodes, redelivery is safe: a redelivered job never
double-creates an external effect.
"""

from __future__ import annotations

import time

from redis.asyncio import Redis


class JobQueue:
    def __init__(
        self,
        redis: Redis,
        *,
        key: str,
        visibility_timeout_seconds: int = 300,
        max_redeliveries: int = 5,
    ) -> None:
        self._redis = redis
        self._pending = key
        self._processing = f"{key}:processing"
        self._heartbeats = f"{key}:heartbeats"
        self._deliveries = f"{key}:deliveries"
        self._dead = f"{key}:dead"
        self._visibility = visibility_timeout_seconds
        self._max_redeliveries = max_redeliveries

    async def enqueue(self, request_id: str) -> None:
        await self._redis.lpush(self._pending, request_id)  # type: ignore[misc]

    async def claim(self, *, timeout_seconds: int = 5) -> str | None:
        """Atomically move the next job to the processing list and return it.

        Blocks up to ``timeout_seconds`` when the queue is empty.
        """

        request_id = await self._redis.blmove(
            self._pending, self._processing, timeout_seconds, "RIGHT", "LEFT"
        )
        if request_id is None:
            return None
        await self._redis.hset(self._heartbeats, request_id, str(time.time()))  # type: ignore[misc]
        return str(request_id)

    async def ack(self, request_id: str) -> None:
        """Remove a finished job from the processing list."""

        await self._redis.lrem(self._processing, 1, request_id)  # type: ignore[misc]
        await self._redis.hdel(self._heartbeats, request_id)  # type: ignore[misc]
        await self._redis.hdel(self._deliveries, request_id)  # type: ignore[misc]

    async def reap(self) -> tuple[int, int]:
        """Redeliver jobs whose claim has expired. Returns (redelivered, dead)."""

        now = time.time()
        redelivered = 0
        dead = 0
        for raw in await self._redis.lrange(self._processing, 0, -1):  # type: ignore[misc]
            request_id = str(raw)
            claimed_at = await self._redis.hget(self._heartbeats, request_id)  # type: ignore[misc]
            age = now - float(claimed_at) if claimed_at else self._visibility + 1
            if age < self._visibility:
                continue
            # Claim ownership of the stale entry; if another reaper beat us, skip.
            if not await self._redis.lrem(self._processing, 1, request_id):  # type: ignore[misc]
                continue
            await self._redis.hdel(self._heartbeats, request_id)  # type: ignore[misc]
            deliveries = await self._redis.hincrby(self._deliveries, request_id, 1)  # type: ignore[misc]
            if deliveries > self._max_redeliveries:
                await self._redis.lpush(self._dead, request_id)  # type: ignore[misc]
                await self._redis.hdel(self._deliveries, request_id)  # type: ignore[misc]
                dead += 1
            else:
                await self._redis.lpush(self._pending, request_id)  # type: ignore[misc]
                redelivered += 1
        return redelivered, dead

    async def depth(self) -> int:
        return int(await self._redis.llen(self._pending))  # type: ignore[misc]

    async def processing_depth(self) -> int:
        return int(await self._redis.llen(self._processing))  # type: ignore[misc]

    async def dead_letter_depth(self) -> int:
        return int(await self._redis.llen(self._dead))  # type: ignore[misc]
