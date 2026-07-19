"""Shared test fixtures.

The suite is fully hermetic: SQLite (in-memory) stands in for Postgres, fakeredis
for Redis, and every integration runs in sandbox mode. No external services, no
secrets. Environment is set before any app module is imported so settings resolve
to the sandbox profile.
"""

from __future__ import annotations

import os

# Must be set before importing app modules (settings are read lazily but caching
# means first read wins). A shared in-memory SQLite DB via StaticPool.
os.environ.update(
    {
        "APP_ENV": "ci",
        "POSTGRES_DSN": "sqlite+aiosqlite://",
        "REDIS_URL": "redis://localhost:6379/0",
        "LLM_MODE": "sandbox",
        "KNOWLEDGE_MODE": "sandbox",
        "SLACK_MODE": "sandbox",
        "JIRA_MODE": "sandbox",
        "EMAIL_MODE": "sandbox",
        "JWT_SECRET": "test-secret-value-please-do-not-use-in-prod",
        "SERVICE_ACCOUNT_ID": "ops-service",
        "SERVICE_ACCOUNT_PASSWORD": "test-password",
        "RATE_LIMIT_REQUESTS": "1000",
        "RATE_LIMIT_WINDOW_SECONDS": "60",
    }
)

import fakeredis.aioredis  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from app.adapters.email.sandbox import SandboxEmail  # noqa: E402
from app.adapters.jira.sandbox import SandboxTickets  # noqa: E402
from app.adapters.knowledge.sandbox import SandboxKnowledge  # noqa: E402
from app.adapters.llm.sandbox import SandboxLlm  # noqa: E402
from app.adapters.slack.sandbox import SandboxNotifier  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.engine import session_scope  # noqa: E402
from app.db.models import Base  # noqa: E402
from app.db.repository import Repository  # noqa: E402
from app.deps import Container, build_container  # noqa: E402
from app.graph.context import NodeConfig, NodeContext  # noqa: E402
from app.main import create_app  # noqa: E402
from app.security.auth import hash_password  # noqa: E402
from app.security.jwt import create_access_token  # noqa: E402


@pytest.fixture
def sandbox_ctx() -> NodeContext:
    """A NodeContext backed entirely by fresh sandbox adapters (no DB needed)."""

    return NodeContext(
        llm=SandboxLlm(),
        knowledge=SandboxKnowledge(),
        tickets=SandboxTickets(project_key="OPS"),
        email=SandboxEmail(),
        notifier=SandboxNotifier(),
        config=NodeConfig(),
    )


@pytest_asyncio.fixture
async def container() -> Container:
    get_settings.cache_clear()
    settings = get_settings()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    built = build_container(settings, redis=redis)

    async with built.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_scope(built.session_factory) as session:
        await Repository(session).upsert_service_account(
            settings.service_account_id,
            hash_password(settings.service_account_password),
            "requests:write,reports:read",
        )

    try:
        yield built
    finally:
        await built.aclose()


@pytest_asyncio.fixture
async def client(container: Container) -> httpx.AsyncClient:
    app = create_app()
    app.state.container = container  # bypass lifespan; inject the test container
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def auth_token() -> str:
    settings = get_settings()
    return create_access_token(
        subject="ops-service",
        scopes=["requests:write", "reports:read"],
        secret=settings.jwt_secret,
        issuer=settings.jwt_issuer,
        ttl_seconds=3600,
    )


@pytest.fixture
def auth_headers(auth_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth_token}"}
