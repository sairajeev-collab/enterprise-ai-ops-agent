# 4. Async job processing via Redis with idempotent nodes

- Status: Accepted
- Date: 2026-07-18

## Context

A single run touches an LLM and several network services and can take tens of
seconds. Blocking an HTTP request for that long is unacceptable: it ties up
workers, breaks under load, and gives the caller no failure isolation. We also
need at-least-once delivery with safe retries, which means steps that perform
side effects must be idempotent.

## Decision

Intake is split from execution. The FastAPI `POST /v1/requests` endpoint
validates input, writes a `request` row with status `queued`, enqueues the
request id onto a Redis list, and returns `202 Accepted` with a status URL. A
separate **worker** process (`app/jobs/worker.py`) does a blocking pop from
Redis, loads the request, runs the LangGraph pipeline, and persists progress
after every node.

Idempotency is enforced two ways:

1. **Deterministic external keys (primary guarantee).** Every side-effecting
   adapter call carries an idempotency key derived from `request_id`
   (`req-<id>`, `req-<id>-reply`, `req-<id>-notify`). Jira dedupes by searching a
   correlation label before creating; email uses a deterministic `Message-ID`;
   Slack and all sandbox adapters dedupe by key. So a node that runs twice —
   whether from an in-run retry or a full re-drive after a crash — produces the
   ticket/email/notification **at most once**. This makes re-execution safe.
2. **Step-level checkpointing (audit + fast-forward).** As the graph streams, the
   worker persists each completed node's output delta to the `run_step` table
   keyed by `(request_id, node_name)`. This is an append-only audit trail and lets
   a re-driven run short-circuit immediately if a terminal step (`report` /
   `needs_review`) is already recorded, avoiding redundant LLM spend in the common
   "crashed right after finishing" case.

Because effects are keyed, we re-drive from the start on retry rather than
attempting fragile mid-graph resumption; the deterministic keys collapse any
repeated effect, and the final persisted record is identical.

A failed run increments an attempt counter; after `MAX_ATTEMPTS` the run is
marked `failed` with the captured error and surfaced via the status endpoint —
we never silently drop work.

## Consequences

- The API stays fast and predictable; heavy work is offloaded.
- Runs survive worker restarts and transient outages.
- Requires running a worker process alongside the API (both in docker-compose,
  both share the same image with different entrypoints).
- Redis is a hard dependency for the async path. A synchronous `?inline=true`
  execution path exists for tests and local debugging.
