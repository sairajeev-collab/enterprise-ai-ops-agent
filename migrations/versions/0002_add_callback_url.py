"""add request.callback_url

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("request", sa.Column("callback_url", sa.String(length=2048), nullable=True))


def downgrade() -> None:
    op.drop_column("request", "callback_url")
