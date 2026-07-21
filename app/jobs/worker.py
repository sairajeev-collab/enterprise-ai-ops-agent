"""The pipeline worker.

Pops request ids from Redis and drives them through the LangGraph pipeline,
checkpointing each node into ``run_step`` and committing the final artifacts in a
single transaction. Transient failures are re-queued up to ``MAX_ATTEMPTS``;
permanent failures are recorded and surfaced. Nodes are idempotent, so a
re-drive never double-creates external effects (ADR-0004).

Run as a module: ``python -m app.jobs.worker``.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import signal
import time
from enum import Enum
from typing import Any

import httpx
from pydantic import BaseModel

from app import metrics
from app.adapters.base import (
    AdapterError,
    NotifyMessage,
    PermanentAdapterError,
    TransientAdapterError,
)
from app.config import get_settings
from app.cost import LlmUsage, open_ledger
from app.db.engine import session_scope
from app.db.repository import Repository
from app.deps import Container, build_container
from app.domain.enums import Channel, RunStatus
from app.domain.state import AgentState
from app.graph.build import NODE_NEEDS_REVIEW, NODE_REPORT, Pipeline
from app.logging import configure_logging, correlation_id, get_logger

logger = get_logger(__name__)

_TERMINAL_STEPS = {NODE_REPORT, NODE_NEEDS_REVIEW}


def to_jsonable(value: Any) -> Any:
    """Convert node deltas (models/enums) into JSON-safe structures for storage."""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    return value


async def process_request(container: Container, request_id: str) -> None:
    """Process one request end-to-end. Safe to call repeatedly for the same id."""

    token = correlation_id.set(request_id)
    try:
        attempts = await _begin(container, request_id)
        if attempts is None:
            return  # already terminal or missing

        state = await _load_state(container, request_id)
        if state is None:
            return

        started = time.perf_counter()
        pipeline = await _select_pipeline(container)
        try:
            final = state
            # Ledger is scoped to this run; the LLM adapters append to it, and we
            # persist the rows after the run so cost is never lost mid-pipeline.
            with open_ledger() as ledger:
                async for node_name, delta in pipeline.stream(state):
                    final = final.model_copy(update=delta)
                    async with session_scope(container.session_factory) as session:
                        await Repository(session).save_step(
                            request_id, node_name, to_jsonable(delta)
                        )
                await finalize_run(container, request_id, final)
            await _persist_cost(container, request_id, final, ledger)
            await fire_callback(container, request_id)
            metrics.JOBS_PROCESSED.labels(status=final.status.value).inc()
            logger.info("request_completed", extra={"status": final.status.value})

        except TransientAdapterError as exc:
            await _handle_failure(container, request_id, exc, attempts, retryable=True)
        except (PermanentAdapterError, AdapterError) as exc:
            await _handle_failure(container, request_id, exc, attempts, retryable=False)
        except Exception as exc:  # noqa: BLE001 - convert to a recorded failure, never crash the loop
            logger.error("request_unexpected_error", exc_info=exc)
            await _handle_failure(container, request_id, exc, attempts, retryable=False)
        finally:
            metrics.JOB_LATENCY.observe(time.perf_counter() - started)
    finally:
        correlation_id.reset(token)


async def _begin(container: Container, request_id: str) -> int | None:
    """Mark the run as running and return the (incremented) attempt count.

    Returns ``None`` if the request is missing or already in a terminal state, or
    if a terminal checkpoint exists (crash-after-finish short-circuit).
    """

    async with session_scope(container.session_factory) as session:
        repo = Repository(session)
        request = await repo.get_request(request_id)
        if request is None:
            logger.warning("request_not_found", extra={"request_id": request_id})
            return None
        if request.status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.NEEDS_REVIEW):
            return None

        steps = await repo.get_completed_steps(request_id)
        if _TERMINAL_STEPS & steps.keys():
            # Pipeline already ran to a terminal node; just settle the status.
            status = RunStatus.NEEDS_REVIEW if NODE_NEEDS_REVIEW in steps else RunStatus.COMPLETED
            await repo.update_request(request, status=status)
            return None

        attempts = request.attempts + 1
        await repo.update_request(request, status=RunStatus.RUNNING, attempts=attempts)
        return attempts


async def _load_state(container: Container, request_id: str) -> AgentState | None:
    async with session_scope(container.session_factory) as session:
        request = await Repository(session).get_request(request_id)
        if request is None:
            return None
        return AgentState(
            request_id=request.id,
            channel=Channel(request.channel),
            raw_subject=request.raw_subject,
            raw_body=request.raw_body,
        )


async def _select_pipeline(container: Container) -> Pipeline:
    """Pick the real pipeline, or the sandbox-only degraded one when today's spend
    has hit the hard cap. This is the cost circuit breaker (ADR-0016): the last line
    of defense against a runaway bill, checked once per run.
    """

    settings = container.settings
    start_of_day = dt.datetime.now(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    async with session_scope(container.session_factory) as session:
        spend = await Repository(session).spend_since(start_of_day)

    if spend >= settings.daily_budget_cap_usd:
        logger.error(
            "budget_cap_tripped",
            extra={"spend_usd": round(spend, 2), "cap_usd": settings.daily_budget_cap_usd},
        )
        metrics.BUDGET_TRIPPED.inc()
        return container.degraded_pipeline
    if spend >= settings.daily_budget_warn_usd:
        logger.warning(
            "budget_warn",
            extra={"spend_usd": round(spend, 2), "warn_usd": settings.daily_budget_warn_usd},
        )
    return container.pipeline


async def _persist_cost(
    container: Container, request_id: str, final: AgentState, ledger: list[LlmUsage]
) -> None:
    request_type = final.classification.request_type.value if final.classification else None
    async with session_scope(container.session_factory) as session:
        repo = Repository(session)
        total = await repo.add_llm_calls(request_id, request_type, ledger)
        request = await repo.get_request(request_id)
        if request is not None:
            await repo.update_request(request, cost_usd=total)
    for usage in ledger:
        if usage.cost_usd:
            metrics.LLM_COST.labels(model=usage.model).inc(usage.cost_usd)


async def finalize_run(container: Container, request_id: str, final: AgentState) -> None:
    async with session_scope(container.session_factory) as session:
        repo = Repository(session)
        request = await repo.get_request(request_id)
        if request is None:
            return

        if final.ticket is not None:
            await repo.add_artifact(
                request_id, kind="ticket", ref=final.ticket.key, payload=final.ticket.model_dump()
            )
        if final.reply is not None:
            await repo.add_artifact(
                request_id,
                kind="reply",
                ref=final.reply.message_id or "",
                payload=final.reply.model_dump(),
            )
        if final.notification_sent:
            await repo.add_artifact(request_id, kind="notification", ref="", payload={"sent": True})
        if final.report is not None:
            await repo.add_artifact(
                request_id, kind="report", ref="", payload={"report": final.report}
            )
        if final.review_reason is not None:
            await repo.add_artifact(
                request_id, kind="review", ref="", payload={"reason": final.review_reason}
            )

        fields: dict[str, Any] = {"status": final.status, "error": None}
        if final.classification is not None:
            fields["request_type"] = final.classification.request_type
            fields["priority"] = final.classification.priority
            fields["confidence"] = final.classification.confidence
        await repo.update_request(request, **fields)


async def fire_callback(container: Container, request_id: str) -> None:
    """POST the final status to the request's callback_url, if it has one.

    Best-effort: a callback failure is logged but never fails the job or the run.

    Security note: callback_url is caller-supplied, so this is an SSRF surface. It
    is validated to be http(s) at intake; a production deployment should further
    restrict it to an allowlist of egress hosts (see README "What I'd do next").
    """

    async with session_scope(container.session_factory) as session:
        request = await Repository(session).get_request(request_id)
        if request is None or not request.callback_url:
            return
        url = request.callback_url
        payload = {
            "request_id": request.id,
            "status": request.status,
            "request_type": request.request_type,
            "priority": request.priority,
            "error": request.error,
            "artifacts": [{"kind": a.kind, "ref": a.ref} for a in request.artifacts],
        }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json=payload)
        logger.info("callback_delivered", extra={"request_id": request_id})
    except httpx.HTTPError as exc:
        logger.warning("callback_failed", extra={"request_id": request_id, "detail": str(exc)})


async def _handle_failure(
    container: Container,
    request_id: str,
    error: Exception,
    attempts: int,
    *,
    retryable: bool,
) -> None:
    max_attempts = container.settings.max_attempts
    async with session_scope(container.session_factory) as session:
        repo = Repository(session)
        request = await repo.get_request(request_id)
        if request is None:
            return

        if retryable and attempts < max_attempts:
            await repo.update_request(
                request, status=RunStatus.QUEUED, error=f"retry {attempts}/{max_attempts}: {error}"
            )
            requeue = True
        else:
            await repo.update_request(request, status=RunStatus.FAILED, error=str(error))
            requeue = False

    if requeue:
        await container.queue.enqueue(request_id)
        metrics.JOBS_PROCESSED.labels(status="requeued").inc()
        logger.warning("request_requeued", extra={"request_id": request_id, "attempt": attempts})
    else:
        metrics.JOBS_PROCESSED.labels(status="failed").inc()
        logger.error("request_failed", extra={"request_id": request_id, "detail": str(error)})
        await fire_callback(container, request_id)


async def run_worker(container: Container | None = None) -> None:
    """Main worker loop. Builds its own container unless one is injected (tests)."""

    settings = get_settings()
    configure_logging(settings.log_level)
    owns_container = container is None
    container = container or build_container(settings)

    # Best-effort: provision knowledge storage. A miss here should not crash the
    # worker — the retrieve node will surface a clear error per request instead.
    with contextlib.suppress(AdapterError):
        await container.node_context.knowledge.ensure_ready()

    stop = asyncio.Event()
    _install_signal_handlers(stop)
    reaper = asyncio.create_task(_reaper_loop(container, stop))
    logger.info("worker_started")

    try:
        while not stop.is_set():
            request_id = await container.queue.claim(timeout_seconds=5)
            if request_id is None:
                continue
            try:
                # A claimed job stays on the processing list until acked; if we
                # crash mid-run the reaper redelivers it. process_request never
                # raises (it records failures), so ack always runs.
                await process_request(container, request_id)
            finally:
                await container.queue.ack(request_id)
    finally:
        logger.info("worker_stopping")
        reaper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reaper
        if owns_container:
            await container.aclose()


async def _reaper_loop(container: Container, stop: asyncio.Event) -> None:
    """Periodically redeliver jobs abandoned by crashed workers."""

    interval = container.settings.job_reaper_interval_seconds
    while not stop.is_set():
        # Wake early if shutdown is signalled; otherwise sweep every interval.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
        if stop.is_set():
            return
        try:
            redelivered, dead = await container.queue.reap()
            if redelivered:
                metrics.JOBS_REDELIVERED.inc(redelivered)
            if dead:
                metrics.JOBS_DEAD_LETTERED.inc(dead)
            if redelivered or dead:
                logger.warning("jobs_reaped", extra={"redelivered": redelivered, "dead": dead})
            metrics.QUEUE_DEPTH.labels(queue="pending").set(await container.queue.depth())
            metrics.QUEUE_DEPTH.labels(queue="processing").set(
                await container.queue.processing_depth()
            )
            metrics.QUEUE_DEPTH.labels(queue="dead_letter").set(
                await container.queue.dead_letter_depth()
            )
            await _check_stuck_jobs(container)
        except Exception as exc:  # noqa: BLE001 - a reaper failure must not kill the worker
            logger.error("reaper_error", exc_info=exc)


async def _check_stuck_jobs(container: Container) -> None:
    """Surface jobs that have been in-flight too long, and page #ops-alerts once."""

    threshold = container.settings.stuck_job_threshold_seconds
    stuck = await container.queue.stuck_jobs(older_than_seconds=threshold)
    metrics.STUCK_JOBS.set(len(stuck))
    if not stuck:
        return

    oldest = max(age for _, age in stuck)
    logger.error("stuck_jobs_detected", extra={"count": len(stuck), "oldest_seconds": oldest})
    ids = ", ".join(request_id for request_id, _ in stuck[:10])
    message = NotifyMessage(
        text=(
            f":warning: {len(stuck)} stuck job(s) processing > {threshold}s "
            f"(oldest {int(oldest)}s): {ids}"
        ),
        channel=container.settings.slack_default_channel,
    )
    # De-duped per reaper interval so we page once, not every sweep. Best-effort:
    # a Slack outage must not take down the reaper.
    window = int(time.time()) // max(container.settings.job_reaper_interval_seconds, 1)
    with contextlib.suppress(AdapterError):
        await container.node_context.notifier.notify(
            message, idempotency_key=f"stuck-alert-{window}"
        )


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            # add_signal_handler is unavailable on Windows event loops.
            loop.add_signal_handler(sig, stop.set)


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_worker())


if __name__ == "__main__":
    main()
