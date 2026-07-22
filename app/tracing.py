"""Optional OpenTelemetry tracing.

The pipeline crosses a process boundary. The API enqueues a job and a separate
worker runs the LangGraph stages, so "why was this one request slow?" is a
distributed-tracing question, not a log-grep question. This module answers it
*when you want it*, and costs nothing when you don't.

Design, mirroring the Sentry seam in ``observability.py``: tracing is opt-in and
dependency-free. With no ``OTEL_EXPORTER_OTLP_ENDPOINT`` set (the default, and all
of CI), :func:`configure_tracing` is a no-op and the OpenTelemetry SDK is never
imported, so the ``otel`` extra is genuinely optional. Set the endpoint and
install the extra, and spans start flowing to a collector.

The :func:`span` context manager is always safe to call. Until tracing is
configured it yields a do-nothing handle, so node and worker code can be
instrumented unconditionally without ``if tracing_enabled`` noise.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol

from app.config import Settings
from app.logging import get_logger

logger = get_logger(__name__)


class _Span(Protocol):
    def set_attribute(self, key: str, value: Any) -> None: ...


class _NoopSpan:
    """Stand-in span used whenever real tracing isn't configured."""

    def set_attribute(self, key: str, value: Any) -> None:  # noqa: D102 - trivial
        return None


# Set once by configure_tracing() if a real tracer is wired up. Kept module-global
# so `span()` needn't thread a tracer through every call site.
_tracer: Any | None = None


def configure_tracing(settings: Settings) -> bool:
    """Wire up OTLP tracing if configured and the SDK is available.

    Returns True if real tracing was enabled, False if this is a no-op (no
    endpoint set, or the optional SDK isn't installed). Imported lazily so the app
    has no hard dependency on OpenTelemetry.
    """

    global _tracer
    if not settings.otel_exporter_otlp_endpoint:
        return False
    try:
        from opentelemetry import trace  # type: ignore[import-not-found]
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore[import-not-found]
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource  # type: ignore[import-not-found]
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import-not-found]
        from opentelemetry.sdk.trace.export import (  # type: ignore[import-not-found]
            BatchSpanProcessor,
        )
    except ImportError:
        # Endpoint set but the extra isn't installed: warn and stay a no-op rather
        # than crashing the process over telemetry.
        logger.warning("otel_endpoint_set_but_sdk_missing")
        return False

    resource = Resource.create({"service.name": settings.otel_service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(settings.otel_service_name)
    logger.info("tracing_initialized", extra={"endpoint": settings.otel_exporter_otlp_endpoint})
    return True


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[_Span]:
    """Open a span if tracing is on, else yield a no-op handle.

    Always safe to call. Attributes are attached up front; the caller can set more
    on the yielded handle as work progresses.
    """

    if _tracer is None:
        yield _NoopSpan()
        return
    with _tracer.start_as_current_span(name) as otel_span:
        for key, value in attributes.items():
            otel_span.set_attribute(key, value)
        yield otel_span


def reset_for_tests() -> None:
    """Drop any configured tracer. Tests only."""

    global _tracer
    _tracer = None
