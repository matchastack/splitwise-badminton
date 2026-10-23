"""opening balances

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-23 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "opening_balances",
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount_cents <> 0", name=op.f("ck_opening_balances_amount_nonzero")),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["members.id"],
            name=op.f("fk_opening_balances_member_id_members"),
        ),
        sa.PrimaryKeyConstraint("member_id", name=op.f("pk_opening_balances")),
    )


def downgrade() -> None:
    op.drop_table("opening_balances")
