"""Shuttlecock batches (FR-SH-1 to FR-SH-4)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from badminton.db.models import Session, ShuttleBatch
from badminton.domain.ledger import EntryKind, Ledger


@dataclass(frozen=True)
class StartOptions:
    """Where a new batch can start: before the next session, or at the last one
    (for shuttles already used in the most recent session)."""

    next_session: Session | None
    last_session: Session | None


async def start_options(db: AsyncSession, now: datetime) -> StartOptions:
    latest_batch_start = await db.scalar(
        select(ShuttleBatch.starts_at).order_by(ShuttleBatch.starts_at.desc()).limit(1)
    )
    scheduled = select(Session).where(Session.status == "scheduled")
    next_session = await db.scalar(
        scheduled.where(Session.starts_at > now).order_by(Session.starts_at).limit(1)
    )
    last_session = await db.scalar(
        scheduled.where(Session.starts_at <= now).order_by(Session.starts_at.desc()).limit(1)
    )
    already_covered = (
        last_session is not None
        and latest_batch_start is not None
        and last_session.starts_at <= latest_batch_start
    )
    if already_covered:
        last_session = None  # it belongs to the latest batch or an earlier one
    return StartOptions(next_session, last_session)


@dataclass(frozen=True)
class ClosedBatch:
    batch_id: int
    payer_id: int
    amount_cents: int
    shares: list[tuple[int, int, int]]  # (member ID, minutes, cents), largest first

    @property
    def minutes(self) -> int:
        return sum(minutes for _, minutes, _ in self.shares)


def closed_batch(ledger: Ledger, batch_id: int) -> ClosedBatch | None:
    """How a closed batch was split, from the ledger's entries."""
    paid = [
        e for e in ledger.entries if e.kind is EntryKind.SHUTTLE_PAID and e.batch_id == batch_id
    ]
    if not paid:
        return None
    shares = [
        (e.member_id, e.minutes or 0, -e.amount_cents)
        for e in ledger.entries
        if e.kind is EntryKind.SHUTTLE_SHARE and e.batch_id == batch_id
    ]
    shares.sort(key=lambda share: (-share[1], share[0]))
    return ClosedBatch(batch_id, paid[0].member_id, paid[0].amount_cents, shares)
