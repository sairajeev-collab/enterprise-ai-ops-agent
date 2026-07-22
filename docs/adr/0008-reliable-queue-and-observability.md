# 8. Reliable delivery, dead-lettering, and observability

- Status: Accepted
- Date: 2026-07-19

## Context

The first cut used a plain Redis `BLPOP` loop. That is **at-most-once**: the job
is removed from Redis the instant it is popped, so a worker killed mid-run (deploy,
OOM, node loss) silently loses that unit of work. For a system that opens tickets
and replies to customers, silently dropping work is unacceptable. We also had no
runtime signal, no metrics, only logs, so operating it on-call was not viable.

## Decision

**Reliable queue (at-least-once).** `app/jobs/queue.py` implements the standard
reliable-queue pattern:

- `claim` atomically moves a job id from the pending list to a `processing` list
  (`BLMOVE`) and records a claim timestamp in a heartbeat hash.
- `ack` removes the job from `processing` once the worker finishes.
- A **reaper** loop (run inside the worker, interval configurable) sweeps
  `processing` and redelivers any job whose claim is older than the visibility
  timeout. I.e. jobs abandoned by a crashed worker.
- A job redelivered more than `JOB_MAX_REDELIVERIES` times is moved to a
  **dead-letter** list for operator inspection instead of looping forever.

Because nodes are idempotent (deterministic external keys, ADR-0004), redelivery
never double-creates a ticket, email, or Slack post. The worker acks in a
`finally` after `process_request` (which never raises. It records failures), so
the only way a job stays on `processing` is a hard crash, which is exactly the
case the reaper covers.

**Graceful shutdown.** `SIGTERM`/`SIGINT` set a stop event; the worker finishes
the in-flight job, acks it, cancels the reaper, and closes resources.

**Observability.** Prometheus metrics (`/metrics`) cover HTTP request rate and
latency, job throughput/latency by terminal status, reaper redeliveries,
dead-letters, and live queue depths. `SENTRY_DSN` remains an optional error sink.

**Ingress hardening.** A middleware rejects requests whose `Content-Length`
exceeds `MAX_REQUEST_BYTES` (413) before they are read, and sets baseline security
headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`).

## Consequences

- Delivery is now at-least-once end-to-end; combined with idempotency, effects are
  effectively exactly-once from the customer's perspective.
- Poison messages are contained on the dead-letter queue rather than blocking the
  pipeline.
- The service is observable enough to alert on (queue depth, dead-letter rate,
  job failure rate, p99 latency).
- Two extra background concerns to run (the reaper task, metric scraping), both in
  the existing worker/API processes, no new deployables.
