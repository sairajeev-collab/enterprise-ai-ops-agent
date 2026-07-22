# Observability

How to see what the system is doing, and what it's costing. Design rationale is in
[ADR-0019](adr/0019-observability.md); this is the operator's how-to.

## The three layers

| Layer | Default | Turn it on |
|-------|---------|-----------|
| Prometheus metrics + Grafana dashboard | always exposed at `/metrics` | `docker compose --profile observability up` |
| Correlated structured logs | always on |. |
| Sentry error tracking | off | set `SENTRY_DSN` |
| OpenTelemetry tracing | off (no-op) | set `OTEL_EXPORTER_OTLP_ENDPOINT`, install `.[otel]` |

## Metrics catalog

Everything the app emits (`app/metrics.py`), and why it exists:

| Metric | Type | What it answers |
|--------|------|-----------------|
| `ops_http_requests_total{method,status}` | counter | request rate, error rate |
| `ops_http_request_duration_seconds{method}` | histogram | API latency (p50/p95) |
| `ops_jobs_processed_total{status}` | counter | pipeline throughput by outcome |
| `ops_job_duration_seconds` | histogram | end-to-end pipeline latency |
| `ops_jobs_redelivered_total` | counter | crash-recovery activity |
| `ops_jobs_dead_lettered_total` | counter | jobs that exhausted retries |
| `ops_queue_depth{queue}` | gauge | backlog (pending/processing/dead_letter) |
| `ops_stuck_jobs` | gauge | in-flight jobs past the stuck threshold |
| `ops_llm_cost_usd_total{model}` | counter | **spend by model** |
| `ops_budget_cap_tripped_total` | counter | runs forced to sandbox by the daily cap |
| `ops_reply_guardrail_blocked_total` | counter | customer replies held before send |

The bundled dashboard (`ops/grafana/dashboards/ops-agent.json`) groups these into
golden signals, pipeline health, and cost/guardrail KPIs.

## Local Grafana

```bash
docker compose --profile observability up
```

- Grafana: <http://localhost:3000> (anonymous admin; dashboard auto-provisioned under "Ops Agent")
- Prometheus: <http://localhost:9090>

> **Honest note:** the dashboard panels are empty until Prometheus has scraped a
> running instance under some traffic. This repo has never run at load. The value
> here is a correct dashboard *definition* wired to the real metric names, not a
> screenshot of invented data.

## Tracing

Off by default and dependency-free. With no `OTEL_EXPORTER_OTLP_ENDPOINT` the OTel
SDK isn't even imported. To turn it on:

```bash
pip install -e ".[otel]"
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318/v1/traces
export OTEL_SERVICE_NAME=ops-agent
```

The worker emits one span per pipeline run (`pipeline.run`), tagged with
`request.id`, `run.status`, and `run.cost_usd`; the FastAPI app is auto-instrumented
so a trace joins the API enqueue to the worker run via the shared request id. The
tracing round-trip against a real collector has **not** been exercised here. It's a
wired seam, labeled as such.

## Correlating a single request

Every response carries `X-Request-ID` (echoing an inbound one if provided). Grep any
log line by it:

```bash
docker compose logs worker | grep <request-id>
```
