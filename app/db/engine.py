"""Async database engine and session factory.

Production runs PostgreSQL via asyncpg. The test suite runs the same ORM metadata
against SQLite (aiosqlite) so the suite is fully hermetic, no database service
required in CI. We special-case SQLite pooling so an in-memory database is shared
across sessions within a test process.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool


def create_engine(dsn: str, *, echo: bool = False) -> AsyncEngine:
    """Build an async engine, applying SQLite-friendly pooling when needed."""

    if dsn.startswith("sqlite"):
        # StaticPool + a single shared connection keeps an in-memory SQLite
        # database alive and consistent across sessions in one process.
        return create_async_engine(
            dsn,
            echo=echo,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_async_engine(dsn, echo=echo, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Provide a transactional session scope: commit on success, roll back on error."""

    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
