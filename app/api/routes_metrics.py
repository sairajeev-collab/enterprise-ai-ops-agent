"""Prometheus scrape endpoint (public).

Exposes process metrics in the Prometheus text format. Left unauthenticated so a
scraper can reach it; in a real deployment it sits on an internal network or is
gated at the ingress.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from app import metrics

router = APIRouter(tags=["observability"])


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    return Response(content=metrics.render(), media_type=metrics.CONTENT_TYPE)
