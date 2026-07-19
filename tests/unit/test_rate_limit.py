"""Unit tests for the Redis-backed rate limiter (using fakeredis)."""

from __future__ import annotations

import fakeredis.aioredis
import pytest
from app.errors import RateLimitError
from app.security.rate_limit import RateLimiter


def _limiter(limit: int) -> RateLimiter:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return RateLimiter(redis, limit=limit, window_seconds=60)


async def test_allows_up_to_limit() -> None:
    limiter = _limiter(limit=3)
    for _ in range(3):
        await limiter.check("client-a")  # no raise


async def test_blocks_over_limit_with_retry_after() -> None:
    limiter = _limiter(limit=2)
    await limiter.check("client-b")
    await limiter.check("client-b")
    with pytest.raises(RateLimitError) as exc:
        await limiter.check("client-b")
    assert exc.value.retry_after > 0


async def test_limits_are_per_identity() -> None:
    limiter = _limiter(limit=1)
    await limiter.check("client-c")
    await limiter.check("client-d")  # different identity, independent bucket
    with pytest.raises(RateLimitError):
        await limiter.check("client-c")
