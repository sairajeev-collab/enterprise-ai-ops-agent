"""Request intake and status endpoints (protected).

``POST /v1/requests`` validates and persists a request, then either enqueues it
for the worker (default, 202), runs it inline (``?inline=true``), or. With
``?inline=true&stream=true``. Runs it inline while streaming per-node LangGraph
progress back to the caller as Server-Sent Events. The row is always committed
before any async work so the worker/stream never observes a missing request.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.api.schemas import (
    ArtifactOut,
    BatchCreateRequest,
    CreateRequest,
    RequestAccepted,
    RequestStatusResponse,
    RequestSummary,
)
from app.db.engine import session_scope
from app.db.models import Request as RequestModel
from app.db.repository import Repository
from app.deps import Container, enforce_rate_limit, get_container, get_repository
from app.domain.enums import Channel, Priority, RequestType, RunStatus
from app.domain.state import AgentState
from app.errors import NotFoundError, ValidationAppError
from app.jobs.worker import finalize_run, fire_callback, process_request, to_jsonable
from app.logging import get_logger
from app.security.auth import SCOPE_REPORTS_READ, SCOPE_REQUESTS_WRITE, get_principal, require_scope
from app.security.jwt import Principal

logger = get_logger(__name__)
router = APIRouter(prefix="/v1", tags=["requests"])

# Canonical node order for the UI's pipeline visualization.
PIPELINE_NODES = [
    "classify",
    "extract",
    "retrieve",
    "create_ticket",
    "draft_reply",
    "notify",
    "persist",
    "generate_report",
]


@router.post("/requests", status_code=202, response_model=None)
async def submit_request(
    payload: CreateRequest,
    request: Request,
    inline: bool = Query(default=False, description="Run synchronously instead of enqueuing."),
    stream: bool = Query(
        default=False, description="Stream node progress as SSE (implies inline)."
    ),
    _principal: Principal = Depends(require_scope(SCOPE_REQUESTS_WRITE)),
    __: None = Depends(enforce_rate_limit),
) -> RequestAccepted | StreamingResponse:
    container = get_container(request)

    async with session_scope(container.session_factory) as session:
        created = await Repository(session).create_request(
            channel=payload.channel.value,
            subject=payload.subject,
            body=payload.body,
            callback_url=payload.callback_url,
        )
        request_id = created.id
        status = created.status

    # Inline + stream: run the pipeline directly and stream progress. We bypass
    # the worker's process_request to avoid two drivers racing on the same run.
    if inline and stream:
        state = AgentState(
            request_id=request_id,
            channel=payload.channel,
            raw_subject=payload.subject,
            raw_body=payload.body,
        )
        return StreamingResponse(
            _sse_pipeline(container, request_id, state),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if inline:
        await process_request(container, request_id)
        async with session_scope(container.session_factory) as session:
            refreshed = await Repository(session).get_request(request_id)
            status = refreshed.status if refreshed else status
    else:
        await container.queue.enqueue(request_id)

    return RequestAccepted(id=request_id, status=status, status_url=f"/v1/requests/{request_id}")


@router.post("/requests/batch", status_code=202, response_model=list[RequestAccepted])
async def submit_batch(
    payload: BatchCreateRequest,
    request: Request,
    _principal: Principal = Depends(require_scope(SCOPE_REQUESTS_WRITE)),
    __: None = Depends(enforce_rate_limit),
) -> list[RequestAccepted]:
    container = get_container(request)

    created: list[tuple[str, RunStatus]] = []
    async with session_scope(container.session_factory) as session:
        repo = Repository(session)
        for item in payload.requests:
            row = await repo.create_request(
                channel=item.channel.value,
                subject=item.subject,
                body=item.body,
                callback_url=item.callback_url,
            )
            created.append((row.id, RunStatus(row.status)))

    for request_id, _status in created:
        await container.queue.enqueue(request_id)

    return [
        RequestAccepted(id=rid, status=st, status_url=f"/v1/requests/{rid}") for rid, st in created
    ]


@router.get("/requests", response_model=list[RequestSummary])
async def list_requests(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    repo: Repository = Depends(get_repository),
    _principal: Principal = Depends(get_principal),
) -> list[RequestSummary]:
    rows = await repo.list_requests(limit=limit, offset=offset)
    return [
        RequestSummary(
            id=r.id,
            channel=Channel(r.channel),
            status=RunStatus(r.status),
            request_type=RequestType(r.request_type) if r.request_type else None,
            priority=Priority(r.priority) if r.priority else None,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


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


@router.post("/requests/{request_id}/retry", status_code=202)
async def retry_request(
    request_id: str,
    request: Request,
    _principal: Principal = Depends(require_scope(SCOPE_REQUESTS_WRITE)),
) -> dict[str, object]:
    """Re-queue a failed request. This is the "get the lost refund out the door"
    button: after a permanent failure or exhausted retries, an operator can push it
    back through once the underlying cause is fixed. Idempotent nodes make the
    re-run safe, no duplicate ticket/email/Slack."""

    container = get_container(request)
    async with session_scope(container.session_factory) as session:
        repo = Repository(session)
        record = await repo.get_request(request_id)
        if record is None:
            raise NotFoundError(f"Request '{request_id}' not found")
        if record.status != RunStatus.FAILED:
            raise ValidationAppError(
                f"only failed requests can be retried (status is '{record.status}')"
            )
        # Fresh start: clear attempts so the re-run gets a full set again.
        await repo.update_request(record, status=RunStatus.QUEUED, attempts=0, error=None)

    await container.queue.enqueue(request_id)
    return {"request_id": request_id, "status": RunStatus.QUEUED.value}


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


# --------------------------------------------------------------------------- #
# SSE streaming
# --------------------------------------------------------------------------- #
def _sse(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _sse_pipeline(
    container: Container, request_id: str, state: AgentState
) -> AsyncIterator[str]:
    yield _sse("stream_start", {"request_id": request_id, "nodes": PIPELINE_NODES})

    async with session_scope(container.session_factory) as session:
        repo = Repository(session)
        request = await repo.get_request(request_id)
        if request is not None:
            await repo.update_request(
                request, status=RunStatus.RUNNING, attempts=request.attempts + 1
            )

    final = state
    # TODO(cost): the streaming path bypasses the worker, so LLM cost isn't logged
    # for stream=true runs and they skip the budget circuit breaker. Low traffic
    # today; wrap this loop in cost.open_ledger + persist like worker.process_request
    # when streaming gets real use. See ADR-0016.
    try:
        async for node_name, delta in container.pipeline.stream(state):
            yield _sse("node_start", {"node": node_name})
            final = final.model_copy(update=delta)
            async with session_scope(container.session_factory) as session:
                await Repository(session).save_step(request_id, node_name, to_jsonable(delta))
            yield _sse("node_delta", {"node": node_name, "output": to_jsonable(delta)})

        await finalize_run(container, request_id, final)
        await fire_callback(container, request_id)
        yield _sse("complete", {"final": _final_summary(final)})
    except Exception as exc:  # noqa: BLE001 - report to the client, never crash the stream
        logger.error("sse_pipeline_error", exc_info=exc)
        async with session_scope(container.session_factory) as session:
            repo = Repository(session)
            request = await repo.get_request(request_id)
            if request is not None:
                await repo.update_request(request, status=RunStatus.FAILED, error=str(exc))
        await fire_callback(container, request_id)
        yield _sse("error", {"detail": str(exc)})


def _final_summary(final: AgentState) -> dict[str, object]:
    classification = final.classification
    return {
        "status": final.status.value,
        "request_type": classification.request_type.value if classification else None,
        "priority": classification.priority.value if classification else None,
        "confidence": classification.confidence if classification else None,
        "ticket": final.ticket.model_dump() if final.ticket else None,
        "reply": final.reply.model_dump() if final.reply else None,
        "notification_sent": final.notification_sent,
        "report": final.report,
        "review_reason": final.review_reason,
    }


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
