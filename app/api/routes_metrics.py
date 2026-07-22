"""Observability endpoints.

``/metrics`` is the Prometheus scrape (public, meant for an internal scraper).
``/metrics/costs`` is a human-readable spend report, that one is business data, so
it needs a token.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from fastapi import APIRouter, Depends, Query, Response

from app import metrics
from app.db.repository import Repository
from app.deps import get_repository
from app.security.auth import SCOPE_REPORTS_READ, require_scope
from app.security.jwt import Principal

router = APIRouter(tags=["observability"])


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    return Response(content=metrics.render(), media_type=metrics.CONTENT_TYPE)


@router.get("/metrics/costs")
async def cost_report(
    days: int = Query(default=7, ge=1, le=90),
    repo: Repository = Depends(get_repository),
    _principal: Principal = Depends(require_scope(SCOPE_REPORTS_READ)),
) -> dict[str, object]:
    """Spend broken down by model, day, and request type over the last N days.

    Aggregation is in Python (see Repository.cost_rows_since). Fine at this volume,
    and it keeps the query DB-agnostic (SQLite dev, Postgres prod). The day this
    table gets big, move it to GROUP BY + date_trunc.
    """

    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    rows = await repo.cost_rows_since(since)

    by_model: dict[str, dict[str, float]] = defaultdict(
        lambda: {"calls": 0, "cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0}
    )
    by_day: dict[str, float] = defaultdict(float)
    by_type: dict[str, float] = defaultdict(float)
    total = 0.0

    for row in rows:
        total += row.cost_usd
        bucket = by_model[row.model]
        bucket["calls"] += 1
        bucket["cost_usd"] += row.cost_usd
        bucket["tokens_in"] += row.tokens_in
        bucket["tokens_out"] += row.tokens_out
        by_day[row.created_at.date().isoformat()] += row.cost_usd
        by_type[row.request_type or "unknown"] += row.cost_usd

    return {
        "since": since.isoformat(),
        "days": days,
        "calls": len(rows),
        "total_usd": round(total, 4),
        "by_model": {
            model: {**vals, "cost_usd": round(vals["cost_usd"], 4)}
            for model, vals in by_model.items()
        },
        "by_day": {day: round(cost, 4) for day, cost in by_day.items()},
        "by_request_type": {rtype: round(cost, 4) for rtype, cost in by_type.items()},
    }
