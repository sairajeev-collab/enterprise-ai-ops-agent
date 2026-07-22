"""Redis-backed fixed-window rate limiter.

Simple and predictable: one counter per (key, window) with a TTL. Fixed windows
can allow a burst at a boundary, which is an acceptable tradeoff here versus the
complexity of a sliding log. The goal is abuse/flood protection, not billing.
The limiter is fail-open on Redis errors (we don't want the limiter to take the
API down), but it logs loudly when that happens.
"""

from __future__ import annotations

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.errors import RateLimitError
from app.logging import get_logger

logger = get_logger(__name__)


class RateLimiter:
    def __init__(self, redis: Redis, *, limit: int, window_seconds: int) -> None:
        self._redis = redis
        self._limit = limit
        self._window = window_seconds

    async def check(self, identity: str) -> None:
        """Count one request for ``identity``; raise if over the limit."""

        bucket = f"ratelimit:{identity}"
        try:
            current = await self._redis.incr(bucket)
            if current == 1:
                await self._redis.expire(bucket, self._window)
            if current > self._limit:
                ttl = await self._redis.ttl(bucket)
                retry_after = ttl if ttl and ttl > 0 else self._window
                raise RateLimitError("Rate limit exceeded", retry_after=retry_after)
        except RedisError as exc:
            # Fail open: a limiter outage must not become an API outage.
            logger.error("rate_limiter_unavailable", exc_info=exc)
