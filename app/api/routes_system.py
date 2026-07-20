"""Operations / admin endpoints for queue visibility and recovery (protected).

These endpoints talk to Redis directly. If Redis is unavailable they return a
clean 502 (``dependency_error``) rather than a generic 500, so an operator can
tell "the queue backend is down" apart from "the service is broken".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from redis.exceptions import RedisError

from app.deps import get_container
from app.errors import DependencyError
from app.security.auth import SCOPE_REPORTS_READ, SCOPE_REQUESTS_WRITE, require_scope
from app.security.jwt import Principal

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/queue")
async def queue_insights(
    request: Request,
    _principal: Principal = Depends(require_scope(SCOPE_REPORTS_READ)),
) -> dict[str, int]:
    queue = get_container(request).queue
    try:
        return {
            "pending": await queue.depth(),
            "processing": await queue.processing_depth(),
            "dead_letter": await queue.dead_letter_depth(),
        }
    except RedisError as exc:
        raise DependencyError("Queue backend (Redis) is unavailable") from exc


@router.post("/queue/replay/{request_id}")
async def replay_dead_letter(
    request_id: str,
    request: Request,
    _principal: Principal = Depends(require_scope(SCOPE_REQUESTS_WRITE)),
) -> dict[str, object]:
    try:
        requeued = await get_container(request).queue.requeue_from_dead(request_id)
    except RedisError as exc:
        raise DependencyError("Queue backend (Redis) is unavailable") from exc
    return {"request_id": request_id, "requeued": requeued}
