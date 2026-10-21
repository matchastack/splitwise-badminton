"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-21 16:51:20.541009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "drafts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("prompt_message_id", sa.Integer(), nullable=True),
        sa.Column(
            "data", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_drafts")),
    )
    op.create_index(op.f("ix_drafts_expires_at"), "drafts", ["expires_at"], unique=False)
    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("card_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("card_message_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoices")),
        sa.UniqueConstraint("period", name=op.f("uq_invoices_period")),
    )
    op.create_table(
        "members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("display_name", sa.String(length=32), nullable=False),
        sa.Column("username", sa.String(length=32), nullable=True),
        sa.Column("dm_enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_members")),
        sa.UniqueConstraint("telegram_user_id", name=op.f("uq_members_telegram_user_id")),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="scheduled", nullable=False),
        sa.Column("card_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("card_message_id", sa.Integer(), nullable=True),
        sa.Column("card_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('scheduled', 'cancelled')", name=op.f("ck_sessions_status")),
        sa.CheckConstraint("ends_at > starts_at", name=op.f("ck_sessions_ends_after_start")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
    )
    op.create_index(op.f("ix_sessions_starts_at"), "sessions", ["starts_at"], unique=False)
    op.create_table(
        "attendance",
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('in', 'out')", name=op.f("ck_attendance_status")),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name=op.f("fk_attendance_member_id_members")
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.id"], name=op.f("fk_attendance_session_id_sessions")
        ),
        sa.PrimaryKeyConstraint("session_id", "member_id", name=op.f("pk_attendance")),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor_member_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_member_id"], ["members.id"], name=op.f("fk_audit_log_actor_member_id_members")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(
        op.f("ix_audit_log_actor_member_id"), "audit_log", ["actor_member_id"], unique=False
    )
    op.create_table(
        "bookings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("payer_member_id", sa.Integer(), nullable=False),
        sa.Column("submitted_by_member_id", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("photo_file_id", sa.String(length=255), nullable=True),
        sa.Column("record_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("record_message_id", sa.Integer(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount_cents > 0", name=op.f("ck_bookings_amount_positive")),
        sa.CheckConstraint("minutes > 0", name=op.f("ck_bookings_minutes_positive")),
        sa.ForeignKeyConstraint(
            ["payer_member_id"], ["members.id"], name=op.f("fk_bookings_payer_member_id_members")
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.id"], name=op.f("fk_bookings_session_id_sessions")
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_member_id"],
            ["members.id"],
            name=op.f("fk_bookings_submitted_by_member_id_members"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bookings")),
    )
    op.create_index(
        op.f("ix_bookings_payer_member_id"), "bookings", ["payer_member_id"], unique=False
    )
    op.create_index(op.f("ix_bookings_session_id"), "bookings", ["session_id"], unique=False)
    op.create_index(
        op.f("ix_bookings_submitted_by_member_id"),
        "bookings",
        ["submitted_by_member_id"],
        unique=False,
    )
    op.create_table(
        "invoice_balances",
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("balance_cents", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["invoices.id"], name=op.f("fk_invoice_balances_invoice_id_invoices")
        ),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name=op.f("fk_invoice_balances_member_id_members")
        ),
        sa.PrimaryKeyConstraint("invoice_id", "member_id", name=op.f("pk_invoice_balances")),
    )
    op.create_table(
        "invoice_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("from_member_id", sa.Integer(), nullable=False),
        sa.Column("to_member_id", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("carried_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("amount_cents > 0", name=op.f("ck_invoice_lines_amount_positive")),
        sa.ForeignKeyConstraint(
            ["from_member_id"], ["members.id"], name=op.f("fk_invoice_lines_from_member_id_members")
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["invoices.id"], name=op.f("fk_invoice_lines_invoice_id_invoices")
        ),
        sa.ForeignKeyConstraint(
            ["to_member_id"], ["members.id"], name=op.f("fk_invoice_lines_to_member_id_members")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoice_lines")),
    )
    op.create_index(
        op.f("ix_invoice_lines_from_member_id"), "invoice_lines", ["from_member_id"], unique=False
    )
    op.create_index(
        op.f("ix_invoice_lines_invoice_id"), "invoice_lines", ["invoice_id"], unique=False
    )
    op.create_index(
        op.f("ix_invoice_lines_to_member_id"), "invoice_lines", ["to_member_id"], unique=False
    )
    op.create_table(
        "shuttle_batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payer_member_id", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount_cents > 0", name=op.f("ck_shuttle_batches_amount_positive")),
        sa.ForeignKeyConstraint(
            ["payer_member_id"],
            ["members.id"],
            name=op.f("fk_shuttle_batches_payer_member_id_members"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shuttle_batches")),
        sa.UniqueConstraint("starts_at", name=op.f("uq_shuttle_batches_starts_at")),
    )
    op.create_index(
        op.f("ix_shuttle_batches_payer_member_id"),
        "shuttle_batches",
        ["payer_member_id"],
        unique=False,
    )
    op.create_table(
        "transfers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("from_member_id", sa.Integer(), nullable=False),
        sa.Column("to_member_id", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("invoice_line_id", sa.Integer(), nullable=True),
        sa.Column("recorded_by_member_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount_cents > 0", name=op.f("ck_transfers_amount_positive")),
        sa.CheckConstraint(
            "from_member_id <> to_member_id", name=op.f("ck_transfers_distinct_members")
        ),
        sa.ForeignKeyConstraint(
            ["from_member_id"], ["members.id"], name=op.f("fk_transfers_from_member_id_members")
        ),
        sa.ForeignKeyConstraint(
            ["invoice_line_id"],
            ["invoice_lines.id"],
            name=op.f("fk_transfers_invoice_line_id_invoice_lines"),
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_member_id"],
            ["members.id"],
            name=op.f("fk_transfers_recorded_by_member_id_members"),
        ),
        sa.ForeignKeyConstraint(
            ["to_member_id"], ["members.id"], name=op.f("fk_transfers_to_member_id_members")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transfers")),
        sa.UniqueConstraint("invoice_line_id", name=op.f("uq_transfers_invoice_line_id")),
    )
    op.create_index(
        op.f("ix_transfers_from_member_id"), "transfers", ["from_member_id"], unique=False
    )
    op.create_index(
        op.f("ix_transfers_recorded_by_member_id"),
        "transfers",
        ["recorded_by_member_id"],
        unique=False,
    )
    op.create_index(op.f("ix_transfers_to_member_id"), "transfers", ["to_member_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_transfers_to_member_id"), table_name="transfers")
    op.drop_index(op.f("ix_transfers_recorded_by_member_id"), table_name="transfers")
    op.drop_index(op.f("ix_transfers_from_member_id"), table_name="transfers")
    op.drop_table("transfers")
    op.drop_index(op.f("ix_shuttle_batches_payer_member_id"), table_name="shuttle_batches")
    op.drop_table("shuttle_batches")
    op.drop_index(op.f("ix_invoice_lines_to_member_id"), table_name="invoice_lines")
    op.drop_index(op.f("ix_invoice_lines_invoice_id"), table_name="invoice_lines")
    op.drop_index(op.f("ix_invoice_lines_from_member_id"), table_name="invoice_lines")
    op.drop_table("invoice_lines")
    op.drop_table("invoice_balances")
    op.drop_index(op.f("ix_bookings_submitted_by_member_id"), table_name="bookings")
    op.drop_index(op.f("ix_bookings_session_id"), table_name="bookings")
    op.drop_index(op.f("ix_bookings_payer_member_id"), table_name="bookings")
    op.drop_table("bookings")
    op.drop_index(op.f("ix_audit_log_actor_member_id"), table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("attendance")
    op.drop_index(op.f("ix_sessions_starts_at"), table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("members")
    op.drop_table("invoices")
    op.drop_index(op.f("ix_drafts_expires_at"), table_name="drafts")
    op.drop_table("drafts")
