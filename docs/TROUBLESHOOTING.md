# Troubleshooting

Real problems you can hit running this repo, and the fix. Ordered roughly by how
often they bite. If something here is wrong, it's a bug in the docs — fix it.

## Setup

### `pip install` fails building a wheel on Python 3.14
Some pinned deps have no 3.14 wheels yet. This project targets **Python 3.12**
(`requires-python`, CI, and the Docker image all use 3.12). Use a 3.12 interpreter:

```bash
uv venv --python 3.12 .venv
uv pip install -e ".[dev]"
```

### `make up` / docker compose can't reach Ollama, or the worker hangs on a real model
Local real-model inference needs meaningful RAM. On a 6 GB machine, `llama3.2:3b`
swaps to disk and takes minutes per call. This is a **hardware limit, not a bug** —
the default `LLM_MODE=sandbox` runs everything with no model server. Only set
`LLM_MODE=real` when you have a box that can hold the model resident. See the README
note on hardware-gated real-model validation.

### Windows: watchman / file-watcher errors on startup
Use the non-reload command (`uvicorn app.main:app` without `--reload`) or run inside
Docker. The reload watcher is a dev convenience, not required to run.

## Auth

### `401 unauthorized` on every request
Mint a dev token first and pass it as a bearer:

```bash
python -m scripts.create_token   # prints a token with requests:write, reports:read
curl -H "Authorization: Bearer <token>" ...
```

### `403 forbidden` on a specific endpoint
The token is valid but missing a scope. `/v1/requests*` needs `requests:write`;
`/metrics/costs` and report reads need `reports:read` (ADR-0006).

### API refuses to start in production
By design (ADR-0013). Startup validation rejects the placeholder JWT secret, a
secret under 32 chars, or a `real`-mode integration missing credentials. Read the
startup error — it names the offending setting.

## Runtime

### A request is stuck in `QUEUED` / nothing happens
The worker isn't running or can't reach Redis. Check `docker compose logs worker`
and that `REDIS_URL` resolves. The API enqueues; only the worker drains the queue.

### A request went to `NEEDS_REVIEW`
Not an error — the classifier's confidence was below threshold, so the graph routed
to human review instead of acting blindly (ADR-0006). The `review` artifact holds
the reason.

### A reply wasn't emailed but the run `COMPLETED`
The output guardrail held it (ADR-0018). Look for `reply_guardrail_blocked` in the
logs and `reply_held: true` in the persisted record; the ticket still opened for a
human to send manually. Common causes: the draft leaked a non-customer email
address, was empty/too long, or echoed prompt scaffolding.

### Everything suddenly runs on the sandbox model in a real deployment
The daily budget cap tripped (ADR-0016). `ops_budget_cap_tripped_total` incremented
and you'll see `budget_cap_tripped` in the logs. The circuit breaker forced the
degraded pipeline to stop a runaway bill. Raise `DAILY_BUDGET_CAP_USD` if the cap is
wrong, or investigate the spend via `GET /metrics/costs`.

### Jobs pile up on the dead-letter queue
They exhausted `MAX_ATTEMPTS` on a permanent error. Inspect them, fix the cause,
then requeue — see [OPERATIONS.md](OPERATIONS.md#draining-the-dead-letter-queue).

### `ops_stuck_jobs > 0`
A job has been in-flight past `STUCK_JOB_THRESHOLD_SECONDS`. The reaper alerts on
this. Usually a worker crashed mid-run; the reaper redelivers abandoned jobs
automatically (ADR-0008). If it persists, a job is genuinely wedging a worker —
check the logs for that request id.

## Observability

### Grafana dashboard panels are empty
Expected until Prometheus has scraped a running instance under load. The dashboard
JSON is a correct definition, not a screenshot of data (ADR-0019/0020). Bring up
`docker compose --profile observability up` and generate traffic.

### Traces aren't showing up
Tracing is opt-in. Set `OTEL_EXPORTER_OTLP_ENDPOINT` **and** install the extra
(`pip install -e ".[otel]"`). Without the endpoint it's a no-op by design.
