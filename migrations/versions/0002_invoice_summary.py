"""invoice summary

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-21 17:38:36.697312
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("summary", sa.Text(), server_default="", nullable=False))


def downgrade() -> None:
    op.drop_column("invoices", "summary")
