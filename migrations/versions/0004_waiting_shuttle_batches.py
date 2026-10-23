"""waiting shuttle batches

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-23 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "shuttle_batches", sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Until now a batch went into use as soon as it was recorded.
    op.execute("UPDATE shuttle_batches SET opened_at = created_at")
    op.alter_column("shuttle_batches", "starts_at", nullable=True)
    op.create_check_constraint(
        op.f("ck_shuttle_batches_start_when_opened"),
        "shuttle_batches",
        "(starts_at IS NULL) = (opened_at IS NULL)",
    )
    op.create_unique_constraint(
        op.f("uq_shuttle_batches_opened_at"),
        "shuttle_batches",
        ["opened_at"],
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    # The old schema can't hold a purchase that isn't in use yet. Nothing is billed
    # for it, so dropping it changes no balance.
    op.execute("DELETE FROM shuttle_batches WHERE opened_at IS NULL")
    op.drop_constraint(op.f("uq_shuttle_batches_opened_at"), "shuttle_batches", type_="unique")
    op.drop_constraint(
        op.f("ck_shuttle_batches_start_when_opened"), "shuttle_batches", type_="check"
    )
    op.alter_column("shuttle_batches", "starts_at", nullable=False)
    op.drop_column("shuttle_batches", "opened_at")
