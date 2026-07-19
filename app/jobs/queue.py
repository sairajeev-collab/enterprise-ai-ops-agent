"""Redis list used as a simple, durable work queue.

We use a plain ``RPUSH`` / ``BLPOP`` list rather than a heavyweight broker: the
workload is a single job type with at-least-once semantics, and idempotent nodes
make redelivery safe. ``dequeue`` blocks with a timeout so the worker loop stays
responsive to shutdown signals.
"""

from __future__ import annotations

from redis.asyncio import Redis


class JobQueue:
    def __init__(self, redis: Redis, *, key: str) -> None:
        self._redis = redis
        self._key = key

    async def enqueue(self, request_id: str) -> None:
        await self._redis.rpush(self._key, request_id)  # type: ignore[misc]

    async def dequeue(self, *, timeout_seconds: int = 5) -> str | None:
        """Block for up to ``timeout_seconds`` for the next request id."""

        result = await self._redis.blpop([self._key], timeout=timeout_seconds)  # type: ignore[misc]
        if result is None:
            return None
        # blpop returns (key, value); with decode_responses the value is a str.
        return str(result[1])

    async def depth(self) -> int:
        return int(await self._redis.llen(self._key))  # type: ignore[misc]
