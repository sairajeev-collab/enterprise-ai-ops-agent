"""Provider failover for the LLM port.

Why: a single LLM provider is a single point of failure. When OpenAI 429s during a
traffic spike, or a region has a bad hour, the choice is "fail the customer's
ticket" or "answer with something". This tries providers in order and falls
through on *transient* errors only. A 400 is a bad request everywhere, so we don't
burn three providers on it.

The default chain ends in the sandbox model. That's deliberate graceful
degradation: a keyword-classified reply from the free model beats a 502 to the
customer. The run is tagged so you can see in the cost log when it happened.

Honest scope (ADR-0016): the chain is generic over ``LlmPort``, but only the
OpenAI-compatible + sandbox links are wired here. Adding Anthropic or Google is a
new adapter in the list, not a rewrite. I haven't built those because I can't test
them without paid keys, and shipping an untested integration is worse than not
shipping it.
"""

from __future__ import annotations

from app.adapters.base import AdapterError, LlmPort, TransientAdapterError
from app.logging import get_logger

logger = get_logger(__name__)


class FailoverLlm(LlmPort):
    def __init__(self, providers: list[tuple[str, LlmPort]]) -> None:
        if not providers:
            raise ValueError("FailoverLlm needs at least one provider")
        self._providers = providers

    async def complete(
        self, *, system: str, user: str, temperature: float = 0.0, json_mode: bool = False
    ) -> str:
        last: TransientAdapterError | None = None
        for label, provider in self._providers:
            try:
                return await provider.complete(
                    system=system, user=user, temperature=temperature, json_mode=json_mode
                )
            except TransientAdapterError as exc:
                last = exc
                logger.warning("llm_failover", extra={"provider": label, "code": exc.code})
        assert last is not None  # loop only exits the try via return or this branch
        raise last

    async def embed(self, texts: list[str]) -> list[list[float]]:
        last: TransientAdapterError | None = None
        for label, provider in self._providers:
            try:
                return await provider.embed(texts)
            except TransientAdapterError as exc:
                last = exc
                logger.warning("llm_failover_embed", extra={"provider": label, "code": exc.code})
        assert last is not None
        raise last

    async def aclose(self) -> None:
        for _, provider in self._providers:
            close = getattr(provider, "aclose", None)
            if close is not None:
                try:
                    await close()
                except AdapterError as exc:  # pragma: no cover - best-effort shutdown
                    logger.warning("llm_close_failed", extra={"detail": str(exc)})
