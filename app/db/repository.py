"""Data-access layer.

All SQL lives here; graph nodes and routes call these methods and never touch the
ORM directly. Each method takes an ``AsyncSession`` so the caller controls the
transaction boundary (see :func:`app.db.engine.session_scope`).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.cost import LlmUsage
from app.db.models import Artifact, LlmCallLog, Request, RunStep, ServiceAccount


class Repository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- service accounts --------------------------------------------------- #
    async def upsert_service_account(
        self, account_id: str, password_hash: str, scopes: str
    ) -> None:
        existing = await self._session.get(ServiceAccount, account_id)
        if existing is None:
            self._session.add(
                ServiceAccount(id=account_id, password_hash=password_hash, scopes=scopes)
            )
        else:
            existing.password_hash = password_hash
            existing.scopes = scopes

    async def get_service_account(self, account_id: str) -> ServiceAccount | None:
        return await self._session.get(ServiceAccount, account_id)

    # --- requests ----------------------------------------------------------- #
    async def create_request(
        self, *, channel: str, subject: str, body: str, callback_url: str | None = None
    ) -> Request:
        request = Request(
            id=str(uuid.uuid4()),
            channel=channel,
            raw_subject=subject,
            raw_body=body,
            status="queued",
            callback_url=callback_url,
        )
        self._session.add(request)
        await self._session.flush()  # populate defaults without ending the txn
        return request

    async def get_request(self, request_id: str) -> Request | None:
        stmt = (
            select(Request)
            .where(Request.id == request_id)
            .options(selectinload(Request.artifacts), selectinload(Request.steps))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_requests(self, *, limit: int = 20, offset: int = 0) -> list[Request]:
        """Return recent requests (newest first) without eager-loading children."""

        stmt = (
            select(Request)
            .order_by(Request.created_at.desc(), Request.id)
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def update_request(self, request: Request, **fields: object) -> None:
        for key, value in fields.items():
            setattr(request, key, value)
        self._session.add(request)

    # --- run steps (idempotency checkpoints) -------------------------------- #
    async def get_completed_steps(self, request_id: str) -> dict[str, dict[str, Any]]:
        stmt = select(RunStep).where(RunStep.request_id == request_id)
        result = await self._session.execute(stmt)
        return {step.node_name: step.output for step in result.scalars()}

    async def save_step(self, request_id: str, node_name: str, output: dict[str, Any]) -> None:
        # Idempotent: if this node was already checkpointed, do nothing.
        stmt = select(RunStep).where(
            RunStep.request_id == request_id, RunStep.node_name == node_name
        )
        existing = (await self._session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return
        self._session.add(RunStep(request_id=request_id, node_name=node_name, output=output))

    # --- artifacts ---------------------------------------------------------- #
    async def add_artifact(
        self, request_id: str, *, kind: str, ref: str, payload: dict[str, Any]
    ) -> None:
        self._session.add(Artifact(request_id=request_id, kind=kind, ref=ref, payload=payload))

    # --- cost accounting ---------------------------------------------------- #
    async def add_llm_calls(
        self, request_id: str, request_type: str | None, usages: list[LlmUsage]
    ) -> float:
        """Persist one run's LLM calls; return the total cost."""

        total = 0.0
        for usage in usages:
            total += usage.cost_usd
            self._session.add(
                LlmCallLog(
                    request_id=request_id,
                    provider=usage.provider,
                    model=usage.model,
                    tokens_in=usage.tokens_in,
                    tokens_out=usage.tokens_out,
                    cost_usd=usage.cost_usd,
                    latency_ms=usage.latency_ms,
                    request_type=request_type,
                )
            )
        return round(total, 6)

    async def spend_since(self, since: dt.datetime) -> float:
        """Total USD spent since ``since``. The number the budget guardrail reads.

        EXPLAIN: index scan on ix_llm_call_log_created_at then a SUM aggregate; the
        created_at index makes the range the selective step. Cheap even with a
        day's worth of rows.
        """

        stmt = select(func.coalesce(func.sum(LlmCallLog.cost_usd), 0.0)).where(
            LlmCallLog.created_at >= since
        )
        return float((await self._session.execute(stmt)).scalar_one())

    async def cost_rows_since(self, since: dt.datetime) -> list[LlmCallLog]:
        """Rows for the reporting endpoint. Aggregation by day/model/type is done in
        Python. Fine at portfolio volume; swap for GROUP BY + date_trunc when this
        table grows past a few hundred-thousand rows."""

        stmt = (
            select(LlmCallLog).where(LlmCallLog.created_at >= since).order_by(LlmCallLog.created_at)
        )
        return list((await self._session.execute(stmt)).scalars())
