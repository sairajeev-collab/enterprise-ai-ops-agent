"""Unit tests for LLM cost accounting."""

from __future__ import annotations

from app.cost import LlmUsage, current_total_usd, estimate_cost, open_ledger, record


def test_estimate_cost_uses_published_pricing() -> None:
    # gpt-4o published: $2.50 / 1M input, $10.00 / 1M output.
    assert estimate_cost("gpt-4o", 1_000_000, 0) == 2.50
    assert estimate_cost("gpt-4o", 1000, 500) == round((1000 * 2.50 + 500 * 10.0) / 1_000_000, 6)
    assert estimate_cost("sandbox", 5000, 5000) == 0.0


def test_unknown_model_is_free_not_a_crash() -> None:
    # We would rather under-count an unpriced model than invent a rate.
    assert estimate_cost("gpt-9-ultra-turbo", 1000, 1000) == 0.0


def test_ledger_records_only_within_scope() -> None:
    usage = LlmUsage(
        provider="openai",
        model="gpt-4o",
        tokens_in=1000,
        tokens_out=500,
        cost_usd=estimate_cost("gpt-4o", 1000, 500),
        latency_ms=42,
    )

    # No ledger open -> record is a no-op, nothing leaks into a global.
    record(usage)
    assert current_total_usd() == 0.0

    with open_ledger() as ledger:
        record(usage)
        record(usage)
        assert len(ledger) == 2
        assert current_total_usd() == round(usage.cost_usd * 2, 6)

    # Ledger closed again.
    assert current_total_usd() == 0.0
