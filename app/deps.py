"""Composition root.

The one place that reads configuration and constructs concrete implementations.
Everything else depends on abstractions. Adapter selection (real vs sandbox) is
driven entirely by ``*_MODE`` settings here, per ADR-0002/0005. The assembled
:class:`Container` is attached to ``app.state`` and reused by request dependencies
and the worker.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.adapters.base import EmailPort, KnowledgePort, LlmPort, NotifierPort, TicketPort
from app.adapters.email.sandbox import SandboxEmail
from app.adapters.email.smtp import SmtpEmail
from app.adapters.jira.rest import JiraRestTickets
from app.adapters.jira.sandbox import SandboxTickets
from app.adapters.knowledge.qdrant_store import QdrantKnowledge
from app.adapters.knowledge.sandbox import SandboxKnowledge
from app.adapters.llm.openai_compatible import OpenAICompatibleLlm
from app.adapters.llm.sandbox import SandboxLlm
from app.adapters.slack.sandbox import SandboxNotifier
from app.adapters.slack.webhook import SlackWebhookNotifier
from app.config import IntegrationMode, Settings
from app.db.engine import create_engine, create_session_factory, session_scope
from app.db.repository import Repository
from app.graph.build import Pipeline
from app.graph.context import NodeConfig, NodeContext
from app.jobs.queue import JobQueue
from app.logging import get_logger
from app.security.rate_limit import RateLimiter

logger = get_logger(__name__)


class _Closable(Protocol):
    async def aclose(self) -> None: ...


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    redis: Redis
    node_context: NodeContext
    pipeline: Pipeline
    queue: JobQueue
    rate_limiter: RateLimiter
    _closables: list[_Closable] = field(default_factory=list)

    async def aclose(self) -> None:
        for closable in self._closables:
            try:
                await closable.aclose()
            except Exception as exc:  # noqa: BLE001 - best-effort shutdown
                logger.warning("closable_shutdown_failed", extra={"detail": str(exc)})
        await self.redis.aclose()
        await self.engine.dispose()


# --------------------------------------------------------------------------- #
# Adapter factories (real vs sandbox by env)
# --------------------------------------------------------------------------- #
def _build_llm(settings: Settings, closables: list[_Closable]) -> LlmPort:
    if settings.llm_mode is IntegrationMode.REAL:
        adapter = OpenAICompatibleLlm(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            chat_model=settings.llm_chat_model,
            embed_model=settings.llm_embed_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
        closables.append(adapter)
        return adapter
    return SandboxLlm()


def _build_knowledge(settings: Settings, llm: LlmPort, closables: list[_Closable]) -> KnowledgePort:
    if settings.knowledge_mode is IntegrationMode.REAL:
        return QdrantKnowledge(
            llm=llm,
            url=settings.qdrant_url,
            collection=settings.qdrant_collection,
            vector_size=settings.qdrant_vector_size,
        )
    return SandboxKnowledge()


def _build_tickets(settings: Settings, closables: list[_Closable]) -> TicketPort:
    if settings.jira_mode is IntegrationMode.REAL:
        adapter = JiraRestTickets(
            base_url=settings.jira_base_url,
            email=settings.jira_email,
            api_token=settings.jira_api_token,
            project_key=settings.jira_project_key,
        )
        closables.append(adapter)
        return adapter
    return SandboxTickets(project_key=settings.jira_project_key)


def _build_email(settings: Settings, closables: list[_Closable]) -> EmailPort:
    if settings.email_mode is IntegrationMode.REAL:
        return SmtpEmail(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            sender=settings.email_from,
        )
    return SandboxEmail()


def _build_notifier(settings: Settings, closables: list[_Closable]) -> NotifierPort:
    if settings.slack_mode is IntegrationMode.REAL:
        adapter = SlackWebhookNotifier(
            webhook_url=settings.slack_webhook_url,
            default_channel=settings.slack_default_channel,
        )
        closables.append(adapter)
        return adapter
    return SandboxNotifier()


def build_node_context(settings: Settings, closables: list[_Closable]) -> NodeContext:
    llm = _build_llm(settings, closables)
    knowledge = _build_knowledge(settings, llm, closables)
    return NodeContext(
        llm=llm,
        knowledge=knowledge,
        tickets=_build_tickets(settings, closables),
        email=_build_email(settings, closables),
        notifier=_build_notifier(settings, closables),
        config=NodeConfig(
            jira_project_key=settings.jira_project_key,
            email_from=settings.email_from,
            slack_channel=settings.slack_default_channel,
            max_attempts=settings.max_attempts,
        ),
    )


def build_container(settings: Settings, *, redis: Redis | None = None) -> Container:
    """Construct the full dependency graph from settings.

    ``redis`` may be injected (tests pass a fakeredis instance); otherwise a real
    client is created from ``REDIS_URL``.
    """

    closables: list[_Closable] = []
    engine = create_engine(settings.postgres_dsn)
    session_factory = create_session_factory(engine)
    redis = redis or Redis.from_url(settings.redis_url, decode_responses=True)
    node_context = build_node_context(settings, closables)
    return Container(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        redis=redis,
        node_context=node_context,
        pipeline=Pipeline(node_context),
        queue=JobQueue(
            redis,
            key=settings.job_queue_key,
            visibility_timeout_seconds=settings.job_visibility_timeout_seconds,
            max_redeliveries=settings.job_max_redeliveries,
        ),
        rate_limiter=RateLimiter(
            redis,
            limit=settings.rate_limit_requests,
            window_seconds=settings.rate_limit_window_seconds,
        ),
        _closables=closables,
    )


# --------------------------------------------------------------------------- #
# FastAPI request dependencies
# --------------------------------------------------------------------------- #
def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    container = get_container(request)
    async with session_scope(container.session_factory) as session:
        yield session


def get_repository(session: AsyncSession = Depends(get_session)) -> Repository:
    return Repository(session)


async def enforce_rate_limit(request: Request) -> None:
    """Rate-limit dependency. Keyed by token subject, falling back to client IP."""

    container = get_container(request)
    identity = _rate_limit_identity(request)
    await container.rate_limiter.check(identity)


def _rate_limit_identity(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1]
        # Stable across processes (unlike builtin hash()); we never store the raw
        # token, only a digest, so the limiter key does not leak credentials.
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]
        return f"token:{digest}"
    client = request.client
    return f"ip:{client.host if client else 'unknown'}"
