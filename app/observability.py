"""Observability wiring: request correlation and an error-tracking hook.

Every request gets a correlation id (honoring an inbound ``X-Request-ID`` if
present) that flows into every log line via the logging contextvar and back out
on the response header. The error-tracking hook is deliberately optional and
dependency-free: if ``SENTRY_DSN`` is set and ``sentry_sdk`` is installed, it is
initialized; otherwise it is a no-op. This keeps the integration seam real
without forcing the dependency.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import Settings
from app.logging import correlation_id, get_logger

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


class CorrelationMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        token = correlation_id.set(request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.info(
                "http_request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "elapsed_ms": elapsed_ms,
                },
            )
            correlation_id.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


def init_error_tracking(settings: Settings) -> None:
    """Initialize Sentry if configured; otherwise a no-op.

    Imported lazily so the app has no hard dependency on an error-tracking SDK.
    """

    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("sentry_dsn_set_but_sdk_missing")
        return
    sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.app_env.value)
    logger.info("error_tracking_initialized")
