"""add llm_call_log and request.cost_usd

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "request",
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
    )
    op.create_table(
        "llm_call_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "request_id",
            sa.String(length=36),
            sa.ForeignKey("request.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("request_type", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_llm_call_log_request_id", "llm_call_log", ["request_id"])
    op.create_index("ix_llm_call_log_model", "llm_call_log", ["model"])
    op.create_index("ix_llm_call_log_created_at", "llm_call_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_call_log_created_at", table_name="llm_call_log")
    op.drop_index("ix_llm_call_log_model", table_name="llm_call_log")
    op.drop_index("ix_llm_call_log_request_id", table_name="llm_call_log")
    op.drop_table("llm_call_log")
    op.drop_column("request", "cost_usd")
