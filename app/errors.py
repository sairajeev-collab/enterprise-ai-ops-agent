"""Typed application exceptions and FastAPI error handlers.

The rule we hold to (per the brief): never swallow exceptions. Everything that
crosses a boundary maps to a typed error with a stable code and an HTTP status,
so callers get actionable, non-leaky responses and operators get structured logs.
Adapter-level exceptions live in ``app.adapters.base``; these are the
application/API-level errors.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.adapters.base import AdapterError
from app.logging import get_logger

logger = get_logger(__name__)


class AppError(Exception):
    """Base class for expected, mapped application errors.

    ``code`` is a stable machine-readable identifier clients can branch on;
    ``status_code`` is the HTTP status to return.
    """

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ValidationAppError(AppError):
    status_code = 422
    code = "validation_error"


class AuthenticationError(AppError):
    status_code = 401
    code = "authentication_error"


class AuthorizationError(AppError):
    status_code = 403
    code = "authorization_error"


class RateLimitError(AppError):
    status_code = 429
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after: int) -> None:
        super().__init__(message, details={"retry_after": retry_after})
        self.retry_after = retry_after


class DependencyError(AppError):
    """A downstream dependency failed in a way we cannot recover from here."""

    status_code = 502
    code = "dependency_error"


def _error_body(code: str, message: str, details: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return body


def register_error_handlers(app: FastAPI) -> None:
    """Attach handlers that convert exceptions into consistent JSON envelopes."""

    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        # Client/business errors: log at info/warning, no stack spam.
        logger.warning("app_error", extra={"code": exc.code, "detail": exc.message})
        headers = {}
        if isinstance(exc, RateLimitError):
            headers["Retry-After"] = str(exc.retry_after)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message, exc.details),
            headers=headers,
        )

    @app.exception_handler(AdapterError)
    async def _handle_adapter_error(_: Request, exc: AdapterError) -> JSONResponse:
        # An adapter error reaching the API layer means the request path hit a
        # dependency it could not degrade around. Surface as 502, log with trace.
        logger.error("adapter_error", extra={"adapter_code": exc.code}, exc_info=exc)
        return JSONResponse(
            status_code=502,
            content=_error_body("dependency_error", "A downstream dependency failed.", {}),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        # Last line of defense. We log the full trace but never leak internals.
        logger.error("unhandled_exception", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=_error_body("internal_error", "An unexpected error occurred.", {}),
        )
