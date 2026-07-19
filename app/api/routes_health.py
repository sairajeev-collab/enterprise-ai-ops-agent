"""Health and readiness endpoint (public).

``/health`` verifies the process is up and its critical dependencies (database,
Redis) are reachable, returning 503 if any check fails so orchestrators can gate
traffic. It is intentionally unauthenticated.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import __version__
from app.db.engine import session_scope
from app.deps import get_container
from app.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    container = get_container(request)
    checks: dict[str, str] = {}

    try:
        async with session_scope(container.session_factory) as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 - health must report, not raise
        logger.warning("health_db_failed", extra={"detail": str(exc)})
        checks["database"] = "error"

    try:
        await container.redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("health_redis_failed", extra={"detail": str(exc)})
        checks["redis"] = "error"

    healthy = all(status == "ok" for status in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "version": __version__,
            "checks": checks,
        },
    )
