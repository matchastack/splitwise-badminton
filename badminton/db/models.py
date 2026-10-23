"""SQLAlchemy models for the entities in requirements §6.4.

Costs, shares and balances are never stored here: they are derived from
these facts by the ledger (FR-LED-7).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    MetaData,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from badminton.domain.timeutil import minutes_between

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

SESSION_STATUSES = ("scheduled", "cancelled")
ATTENDANCE_STATUSES = ("in", "out")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012 - SQLAlchemy reads this class attribute
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
    }


def _created_at() -> Mapped[datetime]:
    return mapped_column(server_default=func.now())


def _member_fk(*, nullable: bool = False) -> Mapped[Any]:
    return mapped_column(ForeignKey("members.id"), nullable=nullable, index=True)


class Member(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    display_name: Mapped[str] = mapped_column(String(32))
    username: Mapped[str | None] = mapped_column(String(32))
    dm_enabled: Mapped[bool] = mapped_column(default=False, server_default="false")
    active: Mapped[bool] = mapped_column(default=True, server_default="true")
    created_at: Mapped[datetime] = _created_at()


class Session(Base):
    """One block of play made of one or more bookings."""

    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ends_after_start"),
        CheckConstraint(f"status IN {SESSION_STATUSES}", name="status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    starts_at: Mapped[datetime] = mapped_column(index=True)
    ends_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(16), default="scheduled", server_default="scheduled")
    card_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    card_message_id: Mapped[int | None]
    card_sent_at: Mapped[datetime | None]
    locked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created_at()

    bookings: Mapped[list[Booking]] = relationship(back_populates="session")
    attendance: Mapped[list[Attendance]] = relationship(back_populates="session")

    @property
    def minutes(self) -> int:
        return minutes_between(self.starts_at, self.ends_at)


class Booking(Base):
    """One court payment. The submitter is the payer (FR-BK-1)."""

    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("minutes > 0", name="minutes_positive"),
        CheckConstraint("amount_cents > 0", name="amount_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), index=True)
    payer_member_id: Mapped[int] = _member_fk()
    submitted_by_member_id: Mapped[int] = _member_fk()
    starts_at: Mapped[datetime]
    minutes: Mapped[int]
    amount_cents: Mapped[int]
    photo_file_id: Mapped[str | None] = mapped_column(String(255))
    record_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    record_message_id: Mapped[int | None]
    cancelled_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created_at()

    session: Mapped[Session] = relationship(back_populates="bookings")


class Attendance(Base):
    __tablename__ = "attendance"
    __table_args__ = (CheckConstraint(f"status IN {ATTENDANCE_STATUSES}", name="status"),)

    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(8))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    session: Mapped[Session] = relationship(back_populates="attendance")


class ShuttleBatch(Base):
    """A shuttlecock purchase covering sessions from ``starts_at`` until the next batch."""

    __tablename__ = "shuttle_batches"
    __table_args__ = (CheckConstraint("amount_cents > 0", name="amount_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    payer_member_id: Mapped[int] = _member_fk()
    amount_cents: Mapped[int]
    starts_at: Mapped[datetime] = mapped_column(unique=True)
    created_at: Mapped[datetime] = _created_at()


class Transfer(Base):
    """Money sent from one member to another: credits the sender, debits the receiver."""

    __tablename__ = "transfers"
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="amount_positive"),
        CheckConstraint("from_member_id <> to_member_id", name="distinct_members"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    from_member_id: Mapped[int] = _member_fk()
    to_member_id: Mapped[int] = _member_fk()
    amount_cents: Mapped[int]
    invoice_line_id: Mapped[int | None] = mapped_column(ForeignKey("invoice_lines.id"), unique=True)
    recorded_by_member_id: Mapped[int | None] = _member_fk(nullable=True)
    created_at: Mapped[datetime] = _created_at()


class OpeningBalance(Base):
    """A member's balance carried over from the spreadsheet the bot replaced (FR-MIG-2)."""

    __tablename__ = "opening_balances"
    __table_args__ = (CheckConstraint("amount_cents <> 0", name="amount_nonzero"),)

    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), primary_key=True)
    amount_cents: Mapped[int]  # signed: credit > 0, debit < 0
    created_at: Mapped[datetime] = _created_at()


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    period: Mapped[str] = mapped_column(String(7), unique=True)  # YYYY-MM
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")  # card header
    card_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    card_message_id: Mapped[int | None]
    created_at: Mapped[datetime] = _created_at()

    lines: Mapped[list[InvoiceLine]] = relationship(back_populates="invoice")
    balances: Mapped[list[InvoiceBalance]] = relationship(back_populates="invoice")


class InvoiceLine(Base):
    """One settle-up transfer. It is paid when a transfer points at it."""

    __tablename__ = "invoice_lines"
    __table_args__ = (CheckConstraint("amount_cents > 0", name="amount_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    from_member_id: Mapped[int] = _member_fk()
    to_member_id: Mapped[int] = _member_fk()
    amount_cents: Mapped[int]
    carried_at: Mapped[datetime | None]

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class InvoiceBalance(Base):
    """A member's closing balance when the invoice was generated (FR-INV-7)."""

    __tablename__ = "invoice_balances"

    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), primary_key=True)
    balance_cents: Mapped[int]

    invoice: Mapped[Invoice] = relationship(back_populates="balances")


class Draft(Base):
    """State of an unfinished wizard, kept in the database because functions are stateless."""

    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int | None]
    prompt_message_id: Mapped[int | None]
    data: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default="{}")
    created_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime] = mapped_column(index=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_member_id: Mapped[int | None] = _member_fk(nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default="{}")
    created_at: Mapped[datetime] = _created_at()
