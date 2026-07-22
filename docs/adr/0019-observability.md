# 19. Observability: metrics, correlation, and optional tracing

- Status: Accepted
- Date: 2026-07-21

## Context

When a request is slow or a job fails, the first question is "where?", and this
system spans two processes (the API enqueues; a separate worker runs the LangGraph
stages), so the answer isn't in one log file. We already had structured logs with a
correlation id and Prometheus metrics. This ADR records the full observability
posture and adds distributed tracing, without bolting on a dependency the project
doesn't need to run.

## Decision

Three layers, in increasing cost-to-operate, each optional above the first:

1. **Metrics (always on).** `app/metrics.py` exposes a Prometheus registry at
   `/metrics`: golden signals (HTTP rate/latency, job throughput/latency) plus the
   business KPIs that actually matter here. LLM spend by model, budget-cap trips,
   dead-letters, stuck jobs, and guardrail holds. A Grafana dashboard built on
   exactly these metric names ships in `ops/grafana/dashboards/ops-agent.json`,
   with Prometheus + Grafana available behind a compose profile
   (`docker compose --profile observability up`).

2. **Correlation + error tracking (on by default; Sentry opt-in).** Every request
   carries an `X-Request-ID` that flows through all log lines and back out on the
   response header (`observability.py`). Sentry initializes only if `SENTRY_DSN` is
   set and the SDK is installed. Otherwise a no-op.

3. **Distributed tracing (opt-in, dependency-free by default).** `app/tracing.py`
   configures OpenTelemetry OTLP export only when `OTEL_EXPORTER_OTLP_ENDPOINT` is
   set; unset (the default, and all of CI), it's a no-op and the OTel SDK is never
   imported. The `otel` extra carries the packages. The worker opens one span per
   pipeline run tagged with the request id, status, and cost; the API is
   auto-instrumented when tracing is live. The `span()` context manager is always
   safe to call, so node/worker code is instrumented unconditionally.

## Why tracing is opt-in, not always on

The honest reason: this is a portfolio system, not a fleet. Forcing an OTel
collector and its SDK onto every `pip install` and CI run would be complexity the
project doesn't yet earn, and the metrics + correlated logs already answer most
"where is it slow" questions across the two processes. The tracing seam is built
and wired so that turning it on is one env var, not a refactor; that's the
staff-level move, not shipping a collector nobody runs.

## Honest limitations

- **No fabricated dashboard screenshot.** The dashboard JSON is real and renders
  against a live Prometheus, but its panels are empty until something is scraping a
  running instance. This repo has never run at traffic, and the README says so.
  The value here is the *definition*, not a screenshot of invented data.
- **Tracing is unverified end-to-end at load.** The wiring is exercised by a no-op
  path in tests; a full collector round-trip has not been run here. It's a seam
  built correctly, labeled as untested under real traffic.
- **Worker metrics in compose share the API scrape target.** For a real
  multi-process deploy the worker needs its own metrics endpoint/port; noted in
  `ops/prometheus.yml`.

## Consequences

- One `/metrics` endpoint and a ready-to-import dashboard cover day-to-day ops.
- Tracing is a flip of one env var when a "why was *this* request slow" question
  actually arrives, with no cost until then.
- The dependency budget stays honest: nothing telemetry-related is required to run
  or test the system.
