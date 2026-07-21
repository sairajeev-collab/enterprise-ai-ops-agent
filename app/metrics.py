"""Prometheus metrics.

Metric objects are process-global singletons (the Prometheus client model). They
are incremented from the API middleware and the worker, and exposed at
``/metrics`` for scraping. Labels are kept low-cardinality on purpose — no request
ids or paths — so the series count stays bounded.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

HTTP_REQUESTS = Counter("ops_http_requests_total", "HTTP requests handled", ["method", "status"])
HTTP_LATENCY = Histogram("ops_http_request_duration_seconds", "HTTP request latency", ["method"])

JOBS_PROCESSED = Counter("ops_jobs_processed_total", "Pipeline jobs processed", ["status"])
JOB_LATENCY = Histogram("ops_job_duration_seconds", "Pipeline job latency")
JOBS_REDELIVERED = Counter(
    "ops_jobs_redelivered_total", "Jobs redelivered by the reaper after a crash"
)
JOBS_DEAD_LETTERED = Counter("ops_jobs_dead_lettered_total", "Jobs parked on the dead-letter queue")

QUEUE_DEPTH = Gauge("ops_queue_depth", "Current queue depth by queue", ["queue"])
STUCK_JOBS = Gauge("ops_stuck_jobs", "In-flight jobs older than the stuck threshold")

# Cost guardrails (ADR-0016).
LLM_COST = Counter("ops_llm_cost_usd_total", "LLM spend in USD by model", ["model"])
BUDGET_TRIPPED = Counter(
    "ops_budget_cap_tripped_total", "Runs forced to the sandbox model by the daily cap"
)

# Output guardrails (ADR-0018): drafted replies held back instead of emailed.
REPLY_GUARDRAIL_BLOCKED = Counter(
    "ops_reply_guardrail_blocked_total", "Customer replies held by the output guardrail"
)

# Re-exported so callers don't import prometheus_client directly.
CONTENT_TYPE = CONTENT_TYPE_LATEST


def render() -> bytes:
    return generate_latest()
