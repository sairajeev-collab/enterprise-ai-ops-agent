"""Performance benchmark harness.

Measures the system's own overhead with the deterministic sandbox model, SQLite,
and fakeredis, in-process. This deliberately isolates orchestration/framework cost
and EXCLUDES real LLM latency and real-database parallelism. In production,
end-to-end latency is dominated by the model call (typically 0.5-3s) and
throughput scales horizontally by adding stateless workers.

What these numbers are good for: the orchestration overhead our code adds, the
non-blocking ingest path's latency, and a capacity model. What they are NOT: a
substitute for load-testing a real deployment.

Run: python -m scripts.benchmark
"""

from __future__ import annotations

import asyncio
import math
import os
import time
import tracemalloc

os.environ.update(
    {
        "APP_ENV": "ci",
        "POSTGRES_DSN": "sqlite+aiosqlite://",
        "LLM_MODE": "sandbox",
        "KNOWLEDGE_MODE": "sandbox",
        "SLACK_MODE": "sandbox",
        "JIRA_MODE": "sandbox",
        "EMAIL_MODE": "sandbox",
        "JWT_SECRET": "benchmark-secret-value-not-for-production-use",
        "RATE_LIMIT_REQUESTS": "100000000",
    }
)

import fakeredis.aioredis  # noqa: E402
import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.engine import session_scope  # noqa: E402
from app.db.models import Base  # noqa: E402
from app.db.repository import Repository  # noqa: E402
from app.deps import Container, build_container  # noqa: E402
from app.domain.state import AgentState  # noqa: E402
from app.jobs.worker import process_request  # noqa: E402
from app.main import create_app  # noqa: E402
from app.security.auth import hash_password  # noqa: E402
from app.security.jwt import create_access_token  # noqa: E402

_SAMPLE = {
    "channel": "email",
    "subject": "Refund",
    "body": "I need a refund for my invoice urgently. from Jane Smith jane@acme.com",
}


def _pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return ordered[int(k)]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def _ms(seconds: float) -> str:
    return f"{seconds * 1000:.1f}ms"


async def _setup() -> tuple[Container, httpx.AsyncClient, dict[str, str]]:
    get_settings.cache_clear()
    settings = get_settings()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    container = build_container(settings, redis=redis)
    async with container.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_scope(container.session_factory) as session:
        await Repository(session).upsert_service_account(
            settings.service_account_id, hash_password("x"), "requests:write,reports:read"
        )
    app = create_app()
    app.state.container = container
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bench")
    token = create_access_token(
        subject="bench",
        scopes=["requests:write", "reports:read"],
        secret=settings.jwt_secret,
        issuer=settings.jwt_issuer,
        ttl_seconds=3600,
    )
    return container, client, {"Authorization": f"Bearer {token}"}


async def _timed_ingest(client: httpx.AsyncClient, headers: dict[str, str], n: int) -> list[float]:
    latencies: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        resp = await client.post("/v1/requests", json=_SAMPLE, headers=headers)
        latencies.append(time.perf_counter() - start)
        assert resp.status_code == 202
    return latencies


async def _timed_pipeline(container: Container, n: int) -> list[float]:
    state = AgentState(
        request_id="bench", channel="email", raw_subject="s", raw_body=_SAMPLE["body"]
    )
    latencies: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        await container.pipeline.run(state)
        latencies.append(time.perf_counter() - start)
    return latencies


async def _worker_throughput(container: Container, n: int) -> float:
    ids: list[str] = []
    async with session_scope(container.session_factory) as session:
        repo = Repository(session)
        for _ in range(n):
            created = await repo.create_request(channel="email", subject="s", body=_SAMPLE["body"])
            ids.append(created.id)
    start = time.perf_counter()
    for request_id in ids:
        await process_request(container, request_id)
    return n / (time.perf_counter() - start)


async def _queue_throughput(container: Container, n: int) -> float:
    start = time.perf_counter()
    for i in range(n):
        await container.queue.enqueue(f"job-{i}")
    for _ in range(n):
        claimed = await container.queue.claim(timeout_seconds=1)
        assert claimed is not None
        await container.queue.ack(claimed)
    return n / (time.perf_counter() - start)


def _row(title: str, latencies: list[float]) -> None:
    rps = len(latencies) / sum(latencies)
    print(
        f"{title:<34} p50 {_ms(_pct(latencies, 0.5)):>8} "
        f"p95 {_ms(_pct(latencies, 0.95)):>8} p99 {_ms(_pct(latencies, 0.99)):>8} "
        f"| {rps:6.0f}/s single-thread"
    )


async def main() -> None:
    container, client, headers = await _setup()
    try:
        # Warm up import/JIT/first-query paths before measuring.
        for _ in range(20):
            await client.post("/v1/requests?inline=true", json=_SAMPLE, headers=headers)

        print("Enterprise AI Operations Agent - benchmark")
        print("sandbox model | SQLite | fakeredis | in-process ASGI | single process\n")

        _row("API ingest (validate+insert+enqueue)", await _timed_ingest(client, headers, 1000))
        _row("Pipeline orchestration (8 nodes)", await _timed_pipeline(container, 500))
        print()

        worker = await _worker_throughput(container, 400)
        queue = await _queue_throughput(container, 5000)
        print(f"{'Worker throughput (pipeline + checkpoints)':<44} {worker:6.0f} jobs/s")
        print(f"{'Reliable queue (enqueue+claim+ack, fakeredis)':<44} {queue:6.0f} ops/s")

        tracemalloc.start()
        await _timed_pipeline(container, 100)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        print(f"{'Peak Python heap over 100 pipelines':<44} {peak / 1_048_576:6.1f} MiB")

        print("\nCapacity model (production):")
        print("  - Ingest is non-blocking; a single API process sustains the rate above,")
        print("    and the API is stateless -> scales horizontally behind a load balancer.")
        print("  - A worker's wall-clock is dominated by the LLM call (~0.5-3s), so real")
        print("    per-worker throughput is ~0.3-2 jobs/s; scale by running N workers.")
        print("  - Orchestration overhead is the p50 above (tens of ms), a small fraction")
        print("    of end-to-end latency in production.")
    finally:
        await client.aclose()
        await container.aclose()


if __name__ == "__main__":
    asyncio.run(main())
