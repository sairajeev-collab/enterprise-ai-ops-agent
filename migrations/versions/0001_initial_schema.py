"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_account",
        sa.Column("id", sa.String(length=128), primary_key=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("scopes", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "request",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("raw_subject", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("raw_body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("request_type", sa.String(length=32), nullable=True),
        sa.Column("priority", sa.String(length=16), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_request_status", "request", ["status"])

    op.create_table(
        "run_step",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "request_id",
            sa.String(length=36),
            sa.ForeignKey("request.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_name", sa.String(length=64), nullable=False),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("request_id", "node_name", name="uq_run_step_node"),
    )
    op.create_index("ix_run_step_request_id", "run_step", ["request_id"])

    op.create_table(
        "artifact",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "request_id",
            sa.String(length=36),
            sa.ForeignKey("request.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("ref", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_artifact_request_id", "artifact", ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_artifact_request_id", table_name="artifact")
    op.drop_table("artifact")
    op.drop_index("ix_run_step_request_id", table_name="run_step")
    op.drop_table("run_step")
    op.drop_index("ix_request_status", table_name="request")
    op.drop_table("request")
    op.drop_table("service_account")
