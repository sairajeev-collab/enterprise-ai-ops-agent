"""Unit tests for provider failover."""

from __future__ import annotations

import pytest
from app.adapters.base import LlmPort, PermanentAdapterError, TransientAdapterError
from app.adapters.llm.failover import FailoverLlm


class _Stub(LlmPort):
    def __init__(self, *, raises: Exception | None = None, reply: str = "ok") -> None:
        self._raises = raises
        self._reply = reply
        self.calls = 0

    async def complete(
        self, *, system: str, user: str, temperature: float = 0.0, json_mode: bool = False
    ) -> str:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._reply

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


async def test_falls_through_to_next_on_transient() -> None:
    primary = _Stub(raises=TransientAdapterError("429 rate limited"))
    fallback = _Stub(reply="from-sandbox")
    llm = FailoverLlm([("openai", primary), ("sandbox", fallback)])

    assert await llm.complete(system="s", user="u") == "from-sandbox"
    assert primary.calls == 1 and fallback.calls == 1


async def test_permanent_error_is_not_failed_over() -> None:
    # A 400 is a bad request everywhere; don't burn the fallback on it.
    primary = _Stub(raises=PermanentAdapterError("400 bad request"))
    fallback = _Stub(reply="never")
    llm = FailoverLlm([("openai", primary), ("sandbox", fallback)])

    with pytest.raises(PermanentAdapterError):
        await llm.complete(system="s", user="u")
    assert fallback.calls == 0


async def test_raises_last_transient_when_all_fail() -> None:
    llm = FailoverLlm(
        [
            ("a", _Stub(raises=TransientAdapterError("a down"))),
            ("b", _Stub(raises=TransientAdapterError("b down"))),
        ]
    )
    with pytest.raises(TransientAdapterError):
        await llm.complete(system="s", user="u")


def test_empty_chain_is_rejected() -> None:
    with pytest.raises(ValueError):
        FailoverLlm([])
