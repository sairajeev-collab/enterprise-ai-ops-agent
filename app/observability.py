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
from starlette.responses import JSONResponse, Response

from app import metrics
from app.config import Settings
from app.logging import correlation_id, get_logger

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


class CorrelationMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        token = correlation_id.set(request_id)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            elapsed = time.perf_counter() - start
            metrics.HTTP_REQUESTS.labels(method=request.method, status=str(status)).inc()
            metrics.HTTP_LATENCY.labels(method=request.method).observe(elapsed)
            logger.info(
                "http_request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "elapsed_ms": round(elapsed * 1000, 2),
                },
            )
            correlation_id.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


class SecurityMiddleware(BaseHTTPMiddleware):
    """Reject oversized requests early and set baseline security headers."""

    def __init__(self, app: object, *, max_body_bytes: int) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._max_body_bytes = max_body_bytes

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        content_length = request.headers.get("content-length")
        if (
            content_length
            and content_length.isdigit()
            and int(content_length) > self._max_body_bytes
        ):
            return JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "payload_too_large",
                        "message": f"Request body exceeds {self._max_body_bytes} bytes",
                    }
                },
                headers=_SECURITY_HEADERS,
            )
        response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
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
