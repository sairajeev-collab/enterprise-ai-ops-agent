"""FastAPI application factory.

Wires middleware, CORS, error handlers, and the composition-root container into a
single app. The container's lifecycle is bound to the app lifespan so datastore
and HTTP connections are opened on startup and closed cleanly on shutdown.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.adapters.base import AdapterError
from app.api.router import api_router
from app.config import Settings, get_settings
from app.db.engine import session_scope
from app.db.repository import Repository
from app.deps import build_container
from app.errors import register_error_handlers
from app.logging import configure_logging, get_logger
from app.observability import CorrelationMiddleware, SecurityMiddleware, init_error_tracking
from app.security.auth import hash_password
from app.tracing import configure_tracing

logger = get_logger(__name__)

# Scopes granted to the bootstrap service account.
_BOOTSTRAP_SCOPES = "requests:write,reports:read"


async def _bootstrap_service_account(app: FastAPI, settings: Settings) -> None:
    """Ensure the configured service account exists so tokens can be minted."""

    container = app.state.container
    async with session_scope(container.session_factory) as session:
        await Repository(session).upsert_service_account(
            settings.service_account_id,
            hash_password(settings.service_account_password),
            _BOOTSTRAP_SCOPES,
        )
    logger.info("service_account_bootstrapped", extra={"id": settings.service_account_id})


def _instrument_fastapi(app: FastAPI) -> None:
    """Auto-instrument HTTP handlers with OTel spans, if the extra is installed.

    Only reached when tracing was successfully configured, so the SDK is present;
    still guarded so a partial install degrades to a warning, not a crash.
    """

    try:
        from opentelemetry.instrumentation.fastapi import (  # type: ignore[import-not-found]
            FastAPIInstrumentor,
        )
    except ImportError:
        logger.warning("otel_fastapi_instrumentation_missing")
        return
    FastAPIInstrumentor.instrument_app(app)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    init_error_tracking(settings)
    if configure_tracing(settings):
        _instrument_fastapi(app)

    app.state.container = build_container(settings)

    # Bootstrap and knowledge provisioning are best-effort: a transient datastore
    # blip at boot should not crash-loop the app. /health reports true readiness.
    with contextlib.suppress(Exception):
        await _bootstrap_service_account(app, settings)
    with contextlib.suppress(AdapterError):
        await app.state.container.node_context.knowledge.ensure_ready()

    try:
        yield
    finally:
        await app.state.container.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Enterprise AI Operations Agent",
        version=__version__,
        summary="Triages inbound work through an explicit LangGraph pipeline.",
        lifespan=lifespan,
    )

    # Middleware is applied outermost-first in reverse registration order, giving:
    # CORS -> correlation/metrics -> body-size + security headers -> app.
    app.add_middleware(SecurityMiddleware, max_body_bytes=settings.max_request_bytes)
    app.add_middleware(CorrelationMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # bearer tokens, not cookies
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    register_error_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
