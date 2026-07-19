"""SQLAlchemy 2.0 ORM models.

Column types are chosen to work identically on PostgreSQL and SQLite so the ORM
metadata is the single source of truth for both production and the test database.
Enums are stored as their string values; timestamps default in Python to stay
DB-agnostic.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Base(DeclarativeBase):
    pass


class ServiceAccount(Base):
    __tablename__ = "service_account"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # Comma-separated scopes, e.g. "requests:write,reports:read".
    scopes: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Request(Base):
    __tablename__ = "request"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel: Mapped[str] = mapped_column(String(32))
    raw_subject: Mapped[str] = mapped_column(String(255), default="")
    raw_body: Mapped[str] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    request_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    priority: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    steps: Mapped[list[RunStep]] = relationship(
        back_populates="request", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list[Artifact]] = relationship(
        back_populates="request", cascade="all, delete-orphan"
    )


class RunStep(Base):
    """A completed pipeline node, checkpointed for idempotent replay."""

    __tablename__ = "run_step"
    __table_args__ = (UniqueConstraint("request_id", "node_name", name="uq_run_step_node"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("request.id", ondelete="CASCADE"), index=True
    )
    node_name: Mapped[str] = mapped_column(String(64))
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    request: Mapped[Request] = relationship(back_populates="steps")


class Artifact(Base):
    """A durable output produced by the pipeline (ticket, reply, report...)."""

    __tablename__ = "artifact"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("request.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    ref: Mapped[str] = mapped_column(String(255), default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    request: Mapped[Request] = relationship(back_populates="artifacts")
