"""Retry-with-backoff for external calls.

Only :class:`TransientAdapterError` is retried — permanent errors fail fast, and
non-adapter exceptions are never caught here (we don't swallow bugs). Backoff is
exponential with optional jitter. ``sleep`` is injectable so tests run instantly
and deterministically.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.adapters.base import TransientAdapterError
from app.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

Sleep = Callable[[float], Awaitable[None]]


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 0.2,
    max_delay: float = 5.0,
    jitter: bool = True,
    sleep: Sleep = asyncio.sleep,
    op_name: str = "external_call",
) -> T:
    """Invoke ``operation`` with exponential backoff on transient failures.

    Raises the last :class:`TransientAdapterError` if all attempts are exhausted;
    re-raises any permanent or unexpected error immediately.
    """

    if attempts < 1:
        raise ValueError("attempts must be >= 1")

    last_error: TransientAdapterError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except TransientAdapterError as exc:
            last_error = exc
            if attempt == attempts:
                break
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            if jitter:
                delay *= 0.5 + random.random()  # noqa: S311 (not cryptographic)
            logger.warning(
                "retrying_external_call",
                extra={"op": op_name, "attempt": attempt, "code": exc.code},
            )
            await sleep(delay)

    assert last_error is not None  # loop only breaks after catching an error
    raise last_error
