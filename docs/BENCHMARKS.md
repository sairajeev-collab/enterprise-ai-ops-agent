# Performance & capacity

This document is deliberately honest about what was measured and what wasn't.

## Methodology

`scripts/benchmark.py` (`make bench`) drives the system in-process with the
deterministic **sandbox** model, **SQLite**, and **fakeredis**. That isolates the
cost *our code* adds and makes the harness runnable anywhere with no
infrastructure. It explicitly does **not** measure:

- real LLM latency (the dominant term in production. Typically 0.5–3 s/call),
- real database concurrency (SQLite serializes through one connection and runs
  every query on a thread-pool executor; production uses PostgreSQL + asyncpg with
  a native-async connection pool),
- multi-process / multi-host scaling.

So the harness answers "how much overhead does the plumbing add, and how does the
non-blocking ingest path behave?", not "what is production throughput?". Getting
the latter honestly requires load-testing a real deployment (see below).

## What was measured (single laptop, single process)

| Metric | Result | Notes |
|--------|--------|-------|
| Pipeline orchestration overhead (8 LangGraph nodes, sandbox) | **~20–35 ms p50 warm** | Pure compute, no I/O. Degrades under sustained single-thread microbenchmark loops (GC + event-loop artifacts); irrelevant in production where each job is an independent, LLM-bound unit of work. |
| Transient memory per pipeline run | **~6 KiB** (0.57 MiB / 100 runs) | Stable across runs; the state object and node deltas are small. |
| Reliable-queue op (enqueue→claim→ack) | hundreds/s on fakeredis | fakeredis is not representative of real Redis latency; treat as a smoke number only. |

Numbers that depend on SQLite/aiosqlite (API ingest RPS, worker jobs/s) were
**intentionally left out of any headline claim** because the thread-executor and
single shared connection make them a property of the harness, not the system.

## Capacity model (the part that actually matters)

The architecture is designed so that performance reasoning is simple:

- **Ingest is non-blocking.** `POST /v1/requests` validates, writes one row, and
  enqueues. O(ms) of work. It never waits on the LLM. The API is stateless, so
  ingest capacity scales horizontally behind a load balancer and is effectively
  bounded by the database's write throughput, not by model latency.
- **Processing is LLM-bound.** A worker's wall-clock per job is dominated by the
  model call `L` (~0.5–3 s). Per-worker throughput ≈ `1/L` ≈ 0.3–2 jobs/s.
- **The queue decouples the two.** Because ingest ≫ processing in throughput, work
  buffers in Redis and is drained by **N stateless workers**, giving `N/L` jobs/s.
  Want 20 jobs/s at `L=1s`? Run ~20 workers. This is the whole reason the design
  is async with a reliable queue (ADR-0004, ADR-0008) rather than doing the work
  inline.

So the two questions an interviewer asks have clean answers: **latency** is
`orchestration (tens of ms) + LLM (L)`, and **throughput** is `N/L`, tuned by
worker count and bounded by the LLM provider's rate limits.

## Getting real numbers (next step)

1. `docker compose up` (PostgreSQL + Redis + Qdrant + real or Ollama LLM).
2. Point `k6` or `locust` at `POST /v1/requests` for the ingest path (real RPS,
   real p95/p99 under concurrency against Postgres).
3. Scale `worker` replicas and watch `ops_jobs_processed_total`,
   `ops_queue_depth`, and `ops_job_duration_seconds` on `/metrics` to measure real
   drain rate under a real model.

The instrumentation (`/metrics`) and the load target (`/v1/requests`) are already
in place; only a deployed environment is missing.
