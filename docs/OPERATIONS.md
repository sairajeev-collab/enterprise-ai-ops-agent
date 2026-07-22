# Operations runbook

What an on-call operator needs to run this service: how to see its state, and the
handful of interventions it supports. Every endpoint below requires a bearer token
with the noted scope (ADR-0006); mint one with `python -m scripts.create_token`.

> These are the procedures the code actually supports today, not an aspirational
> runbook. Where a capability is missing, it says so.

## Health & state at a glance

| Question | Where |
|----------|-------|
| Is the service up and are its deps reachable? | `GET /health` |
| What's the queue doing? | `GET /system/queue` → `{pending, processing, dead_letter, stuck}` |
| What have we spent? | `GET /metrics/costs?days=7` (scope `reports:read`) |
| Everything, for Prometheus | `GET /metrics` |
| One request's full journey | grep logs by its `X-Request-ID` |

For dashboards, bring up Grafana: `docker compose --profile observability up`
(see [OBSERVABILITY.md](OBSERVABILITY.md)).

## Common interventions

### Retry a failed request
After you've fixed the underlying cause of a permanent failure:

```bash
curl -X POST -H "Authorization: Bearer <token>" \
  https://<host>/v1/requests/<request_id>/retry
```

Resets the request to `QUEUED` with a fresh attempt count and re-enqueues it. Only
works on `FAILED` requests. **Safe to do:** the pipeline's idempotency keys mean the
re-run won't double-open a ticket or re-send an email (ADR-0017).

### Draining the dead-letter queue
Jobs that exhausted `MAX_ATTEMPTS` land on the dead-letter list. Check the count
with `GET /system/queue`, then replay a specific one once the cause is fixed:

```bash
curl -X POST -H "Authorization: Bearer <token>" \
  https://<host>/system/queue/replay/<request_id>
```

There is **no bulk drain**. Replay is per id, on purpose, so an operator looks at
each poisoned job before re-driving it. If you don't know the ids, grep the worker
logs for `jobs_reaped` / dead-letter entries.

### Responding to a tripped budget cap
Symptom: runs silently switch to the sandbox model; `ops_budget_cap_tripped_total`
climbs; logs show `budget_cap_tripped`. This is the cost circuit breaker doing its
job (ADR-0016). Steps:

1. Confirm spend: `GET /metrics/costs?days=1`.
2. If the spend is legitimate and the cap is too low, raise `DAILY_BUDGET_CAP_USD`
   and restart. If it's a runaway, find the offending request type in the cost
   breakdown before lifting the cap.
3. The warn threshold (`DAILY_BUDGET_WARN_USD`) logs `budget_warn` earlier. Treat
   that as the real page; the cap is the backstop.

### Reviewing held replies
A `COMPLETED` run whose reply wasn't sent was held by the output guardrail
(ADR-0018): `reply_held: true` in the record, `ops_reply_guardrail_blocked_total`
incremented. The ticket is open; a human sends the reply manually after checking why
it was held (leak, empty/runaway, or scaffolding echo). There is **no reviewer-queue
UI yet**. Held replies surface via the metric and the persisted record.

### Stuck / abandoned jobs
`ops_stuck_jobs > 0` means a job has been in-flight past
`STUCK_JOB_THRESHOLD_SECONDS`. The reaper redelivers jobs abandoned by crashed
workers automatically (ADR-0008) and alerts to `#ops-alerts`. If the count doesn't
clear, one job is wedging a worker. Find it by request id in the logs and, if
needed, let it fail and `retry` it.

## Deploys

One image, two commands (ADR-0015). Migrations run on the API container's start
command, so schema and code move together. Reference host is Fly.io (`fly.toml`);
`docker compose up` reproduces the full stack locally. There is no blue/green or
canary automation here. A deploy is a rolling image swap; for a two-process service
that is deliberately all this needs (scaling story is in the README's "what I'd do
next").

## Key configuration knobs

| Env var | Default | Effect |
|---------|---------|--------|
| `MAX_ATTEMPTS` | 3 | retries before dead-letter |
| `STUCK_JOB_THRESHOLD_SECONDS` | 1800 | when a job counts as stuck |
| `DAILY_BUDGET_WARN_USD` | 50 | spend that logs a warning |
| `DAILY_BUDGET_CAP_USD` | 100 | spend that forces the sandbox model |
| `LLM_MODE` | `sandbox` | `real` to hit a live provider |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | set to enable tracing |
