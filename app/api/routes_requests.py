"""Request intake and status endpoints (protected).

``POST /v1/requests`` validates and persists the request, then either enqueues it
for the worker (default, returns 202) or runs it inline (``?inline=true``, used by
tests and local debugging). The row is committed before the job is enqueued so the
worker can never observe a missing request.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.api.schemas import (
    ArtifactOut,
    CreateRequest,
    RequestAccepted,
    RequestStatusResponse,
)
from app.db.engine import session_scope
from app.db.models import Request as RequestModel
from app.db.repository import Repository
from app.deps import enforce_rate_limit, get_container, get_repository
from app.domain.enums import Channel, Priority, RequestType, RunStatus
from app.errors import NotFoundError
from app.jobs.worker import process_request
from app.security.auth import SCOPE_REPORTS_READ, SCOPE_REQUESTS_WRITE, get_principal, require_scope
from app.security.jwt import Principal

router = APIRouter(prefix="/v1", tags=["requests"])


@router.post("/requests", status_code=202, response_model=RequestAccepted)
async def submit_request(
    payload: CreateRequest,
    request: Request,
    inline: bool = Query(default=False, description="Run synchronously instead of enqueuing."),
    _principal: Principal = Depends(require_scope(SCOPE_REQUESTS_WRITE)),
    __: None = Depends(enforce_rate_limit),
) -> RequestAccepted:
    container = get_container(request)

    # Persist and COMMIT before doing anything async so the worker/inline run
    # always sees a durable row.
    async with session_scope(container.session_factory) as session:
        created = await Repository(session).create_request(
            channel=payload.channel.value, subject=payload.subject, body=payload.body
        )
        request_id = created.id
        status = created.status

    if inline:
        await process_request(container, request_id)
        async with session_scope(container.session_factory) as session:
            refreshed = await Repository(session).get_request(request_id)
            status = refreshed.status if refreshed else status
    else:
        await container.queue.enqueue(request_id)

    return RequestAccepted(id=request_id, status=status, status_url=f"/v1/requests/{request_id}")


@router.get("/requests/{request_id}", response_model=RequestStatusResponse)
async def get_request_status(
    request_id: str,
    repo: Repository = Depends(get_repository),
    _principal: Principal = Depends(get_principal),
) -> RequestStatusResponse:
    record = await repo.get_request(request_id)
    if record is None:
        raise NotFoundError(f"Request '{request_id}' not found")
    return _to_status(record)


@router.get("/requests/{request_id}/report")
async def get_request_report(
    request_id: str,
    repo: Repository = Depends(get_repository),
    _principal: Principal = Depends(require_scope(SCOPE_REPORTS_READ)),
) -> dict[str, object]:
    record = await repo.get_request(request_id)
    if record is None:
        raise NotFoundError(f"Request '{request_id}' not found")
    report = next((a.payload.get("report") for a in record.artifacts if a.kind == "report"), None)
    if report is None:
        raise NotFoundError("No report available for this request yet")
    return {"request_id": request_id, "report": report}


def _to_status(record: RequestModel) -> RequestStatusResponse:
    artifacts = [
        ArtifactOut(kind=a.kind, ref=a.ref, payload=a.payload, created_at=a.created_at)
        for a in sorted(record.artifacts, key=lambda a: a.created_at)
    ]
    return RequestStatusResponse(
        id=record.id,
        channel=Channel(record.channel),
        status=RunStatus(record.status),
        request_type=RequestType(record.request_type) if record.request_type else None,
        priority=Priority(record.priority) if record.priority else None,
        confidence=record.confidence,
        attempts=record.attempts,
        error=record.error,
        created_at=record.created_at,
        updated_at=record.updated_at,
        artifacts=artifacts,
    )
