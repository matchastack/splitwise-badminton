"""Load ledger facts from the database (FR-LED-7): costs are never stored, only derived."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from badminton.db.models import Booking, OpeningBalance, Session, ShuttleBatch, Transfer
from badminton.domain.ledger import (
    BatchFact,
    BookingFact,
    Ledger,
    OpeningFact,
    SessionFact,
    TransferFact,
    compute_ledger,
)


@dataclass(frozen=True)
class LedgerFacts:
    sessions: tuple[SessionFact, ...]
    batches: tuple[BatchFact, ...]
    transfers: tuple[TransferFact, ...]
    openings: tuple[OpeningFact, ...] = ()

    def ledger(self, as_of: datetime) -> Ledger:
        return compute_ledger(
            sessions=self.sessions,
            transfers=self.transfers,
            batches=self.batches,
            openings=self.openings,
            as_of=as_of,
        )


async def load_facts(db: AsyncSession) -> LedgerFacts:
    sessions = await db.scalars(
        select(Session)
        .options(selectinload(Session.bookings), selectinload(Session.attendance))
        .order_by(Session.starts_at, Session.id)
    )
    batches = await load_batches(db)
    transfers = await db.scalars(select(Transfer).order_by(Transfer.created_at, Transfer.id))
    openings = await db.scalars(select(OpeningBalance).order_by(OpeningBalance.member_id))
    return LedgerFacts(
        sessions=tuple(session_fact(session) for session in sessions),
        batches=batches,
        transfers=tuple(
            TransferFact(t.id, t.from_member_id, t.to_member_id, t.amount_cents, t.created_at)
            for t in transfers
        ),
        openings=tuple(OpeningFact(o.member_id, o.amount_cents, o.created_at) for o in openings),
    )


async def load_batches(db: AsyncSession) -> tuple[BatchFact, ...]:
    """Every shuttle batch, sorted by start."""
    batches = await db.scalars(select(ShuttleBatch).order_by(ShuttleBatch.starts_at))
    return tuple(
        BatchFact(b.id, b.payer_member_id, b.amount_cents, b.starts_at, b.created_at)
        for b in batches
    )


def session_fact(session: Session) -> SessionFact:
    """Needs ``bookings`` and ``attendance`` loaded."""
    return SessionFact(
        id=session.id,
        starts_at=session.starts_at,
        ends_at=session.ends_at,
        bookings=tuple(
            BookingFact(booking.payer_member_id, booking.amount_cents)
            for booking in sorted(session.bookings, key=lambda b: b.id)
            if booking.cancelled_at is None
        ),
        attendee_ids=frozenset(a.member_id for a in session.attendance if a.status == "in"),
        cancelled=session.status == "cancelled",
    )


async def load_ledger(db: AsyncSession, as_of: datetime) -> Ledger:
    return (await load_facts(db)).ledger(as_of)


async def get_balances(db: AsyncSession, as_of: datetime) -> dict[int, int]:
    return (await load_ledger(db, as_of)).balances


async def last_hourly_rate(db: AsyncSession, default_cents: int) -> int:
    """Cents per hour of the most recently recorded booking, to suggest a price (FR-BK-6)."""
    latest = await db.scalar(
        select(Booking)
        .where(Booking.cancelled_at.is_(None))
        .order_by(Booking.created_at.desc(), Booking.id.desc())
        .limit(1)
    )
    if latest is None:
        return default_cents
    return (latest.amount_cents * 60 + latest.minutes // 2) // latest.minutes
