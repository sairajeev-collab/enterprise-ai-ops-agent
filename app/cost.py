"""LLM cost accounting.

Why this exists: an LLM pipeline's spend is unbounded by default. One misconfigured
model or a retry storm can turn a $3/day workload into a surprise bill overnight,
and you find out when finance emails you, not when it happens. So every model call
is priced and logged, and there's a hard daily cap that trips the pipeline back to
the free sandbox model before the bill runs away.

Two moving parts:

* **Pricing** — published per-token rates, kept as plain constants. These are NOT
  measured from our traffic; they're OpenAI's list prices and must be updated by
  hand when the vendor changes them (there is no pricing API). Sandbox is free.
* **A request-scoped ledger** — a contextvar the adapters append to on every call.
  The worker opens it around a run, then persists the accumulated rows. Using a
  contextvar (not a return value) keeps ``LlmPort.complete`` a plain ``-> str`` so
  no node signature changes, and it's request-isolated under asyncio.
"""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator

from pydantic import BaseModel

# USD per 1,000,000 tokens (input, output). Source: OpenAI published pricing.
# Keep in sync by hand — vendor has no pricing endpoint. See ADR-0016.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
    # Local / OSS models cost nothing to call. Listed so they don't fall through
    # to the "unknown model" path and get silently mispriced.
    "llama3.1": (0.0, 0.0),
    "nomic-embed-text": (0.0, 0.0),
    "sandbox": (0.0, 0.0),
}


class LlmUsage(BaseModel):
    """One model call's usage and cost, recorded by an adapter."""

    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Price a call from published rates. Unknown models are treated as free and
    flagged by the caller — we would rather under-count than invent a number."""

    in_rate, out_rate = PRICING.get(model, (0.0, 0.0))
    return round((tokens_in * in_rate + tokens_out * out_rate) / 1_000_000, 6)


# Request-scoped accumulator. ``None`` means "no ledger open" — adapters called
# outside a run (e.g. a one-off script) simply don't record, rather than leaking
# into a global list.
_ledger: contextvars.ContextVar[list[LlmUsage] | None] = contextvars.ContextVar(
    "llm_cost_ledger", default=None
)


@contextlib.contextmanager
def open_ledger() -> Iterator[list[LlmUsage]]:
    """Open a fresh ledger for the current context (the worker wraps a run in this)."""

    entries: list[LlmUsage] = []
    token = _ledger.set(entries)
    try:
        yield entries
    finally:
        _ledger.reset(token)


def record(usage: LlmUsage) -> None:
    ledger = _ledger.get()
    if ledger is not None:
        ledger.append(usage)


def current_total_usd() -> float:
    ledger = _ledger.get()
    return round(sum(u.cost_usd for u in ledger), 6) if ledger else 0.0
