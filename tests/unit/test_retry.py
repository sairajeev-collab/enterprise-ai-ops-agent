"""Unit tests for the retry-with-backoff helper."""

from __future__ import annotations

import pytest
from app.adapters.base import PermanentAdapterError, TransientAdapterError
from app.graph.retry import retry_async


async def _no_sleep(_: float) -> None:
    return None


async def test_succeeds_after_transient_failures() -> None:
    calls = {"n": 0}

    async def op() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransientAdapterError("blip")
        return "ok"

    result = await retry_async(op, attempts=5, base_delay=0.0, jitter=False, sleep=_no_sleep)
    assert result == "ok"
    assert calls["n"] == 3


async def test_raises_after_exhausting_attempts() -> None:
    calls = {"n": 0}

    async def op() -> str:
        calls["n"] += 1
        raise TransientAdapterError("always")

    with pytest.raises(TransientAdapterError):
        await retry_async(op, attempts=3, base_delay=0.0, jitter=False, sleep=_no_sleep)
    assert calls["n"] == 3


async def test_permanent_error_not_retried() -> None:
    calls = {"n": 0}

    async def op() -> str:
        calls["n"] += 1
        raise PermanentAdapterError("nope")

    with pytest.raises(PermanentAdapterError):
        await retry_async(op, attempts=5, base_delay=0.0, jitter=False, sleep=_no_sleep)
    assert calls["n"] == 1


async def test_invalid_attempts_rejected() -> None:
    with pytest.raises(ValueError):
        await retry_async(lambda: _ok(), attempts=0)


async def _ok() -> str:
    return "ok"
