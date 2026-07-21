"""Tracing seam unit tests (ADR-0019).

The contract that matters for correctness: tracing is a safe no-op unless an OTLP
endpoint is configured, and `span()` is always callable regardless.
"""

from __future__ import annotations

from app.config import Settings
from app.tracing import configure_tracing, reset_for_tests, span


def test_configure_tracing_is_noop_without_endpoint() -> None:
    reset_for_tests()
    # Local defaults have no OTLP endpoint -> tracing stays a no-op.
    assert configure_tracing(Settings(otel_exporter_otlp_endpoint="")) is False


def test_span_is_usable_when_tracing_off() -> None:
    reset_for_tests()
    # No exception, and the yielded handle accepts attributes silently.
    with span("pipeline.run", **{"request.id": "abc"}) as handle:
        handle.set_attribute("run.status", "completed")
