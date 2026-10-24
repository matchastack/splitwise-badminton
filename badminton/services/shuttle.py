"""Shuttlecock batches (FR-SH-1 to FR-SH-7).

A purchase waits until the admin finishes the batch in use; then it is opened and
covers sessions from its start point. At most one purchase waits at a time.
"""

from __future__ import annotations

import html
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from badminton.db.models import Member, Session, ShuttleBatch
from badminton.domain.ledger import EntryKind, Ledger
from badminton.domain.money import fmt_sgd
from badminton.domain.timeutil import fmt_hours
from badminton.services.repo import load_ledger


@dataclass(frozen=True)
class StartOptions:
    """Where a batch put in use can start: before the next session, or at the last one
    (for shuttles already used in the most recent session)."""

    next_session: Session | None
    last_session: Session | None


async def start_options(db: AsyncSession, now: datetime) -> StartOptions:
    latest_batch_start = await db.scalar(
        select(ShuttleBatch.starts_at)
        .where(ShuttleBatch.starts_at.is_not(None))
        .order_by(ShuttleBatch.starts_at.desc())
        .limit(1)
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


async def waiting_batch(db: AsyncSession) -> ShuttleBatch | None:
    """The purchase waiting to be put in use, if any."""
    return await db.scalar(select(ShuttleBatch).where(ShuttleBatch.opened_at.is_(None)))


async def current_batch(db: AsyncSession) -> ShuttleBatch | None:
    """The batch in use: the latest-starting one that has been opened."""
    return await db.scalar(
        select(ShuttleBatch)
        .where(ShuttleBatch.opened_at.is_not(None))
        .order_by(ShuttleBatch.starts_at.desc())
        .limit(1)
    )


async def next_start(db: AsyncSession, batch: ShuttleBatch) -> datetime | None:
    """When the batch after an opened one starts: ``None`` while ``batch`` is in use."""
    return await db.scalar(
        select(func.min(ShuttleBatch.starts_at)).where(ShuttleBatch.starts_at > batch.starts_at)
    )


async def open_waiting(
    db: AsyncSession, batch_id: int, starts_at: datetime, now: datetime
) -> ShuttleBatch | None:
    """Put the waiting batch in use from ``starts_at``, which finishes the batch in use.
    ``None`` if it is no longer waiting (removed, or opened meanwhile)."""
    opened = await db.scalar(
        update(ShuttleBatch)
        .where(ShuttleBatch.id == batch_id, ShuttleBatch.opened_at.is_(None))
        .values(starts_at=starts_at, opened_at=now)
        .returning(ShuttleBatch.id)
    )
    if opened is None:
        return None
    return await db.get(ShuttleBatch, opened, populate_existing=True)


def waiting_refusal(waiting: ShuttleBatch) -> str:
    return f"Batch #{waiting.id} is waiting, and only one batch can wait. Remove it first."


class AlreadyWaiting(ValueError):
    """Another purchase already waits (FR-SH-5)."""

    def __init__(self, waiting: ShuttleBatch) -> None:
        super().__init__(waiting_refusal(waiting))
        self.waiting = waiting


@asynccontextmanager
async def _making_one_wait(db: AsyncSession) -> AsyncIterator[None]:
    """Save the body's changes, which make a batch wait, in a savepoint. If another batch
    already waits, the database refuses it (FR-SH-5): only the savepoint is rolled back,
    and ``AlreadyWaiting`` is raised. This holds when two requests race, too."""
    try:
        async with db.begin_nested():
            yield
    except IntegrityError:
        if (waiting := await waiting_batch(db)) is None:
            raise  # some other constraint
        raise AlreadyWaiting(waiting) from None


async def add_waiting(db: AsyncSession, *, payer_id: int, amount_cents: int) -> ShuttleBatch:
    """Record a purchase as the waiting batch, or raise ``AlreadyWaiting``."""
    batch = ShuttleBatch(payer_member_id=payer_id, amount_cents=amount_cents)
    async with _making_one_wait(db):
        db.add(batch)
    return batch


class Removal(Enum):
    REMOVED = "removed"
    IN_USE = "in use"  # it went into use meanwhile
    GONE = "gone"  # someone else removed it first


async def remove_waiting(db: AsyncSession, batch: ShuttleBatch) -> Removal:
    """Delete a purchase that is still waiting. ``batch`` stays readable for the
    announcement."""
    removed = await db.scalar(
        delete(ShuttleBatch)
        .where(ShuttleBatch.id == batch.id, ShuttleBatch.opened_at.is_(None))
        .returning(ShuttleBatch.id)
        .execution_options(synchronize_session=False)
    )
    if removed is not None:
        db.expunge(batch)
        return Removal.REMOVED
    if await db.scalar(select(ShuttleBatch.id).where(ShuttleBatch.id == batch.id)) is None:
        db.expunge(batch)
        return Removal.GONE
    await db.refresh(batch)
    return Removal.IN_USE


class ReopenRefused(ValueError):
    """The batch can't go back to waiting; the message says why."""


async def previous_batch(db: AsyncSession, batch: ShuttleBatch) -> ShuttleBatch | None:
    """The batch in use before ``batch`` started."""
    return await db.scalar(
        select(ShuttleBatch)
        .where(ShuttleBatch.starts_at < batch.starts_at)
        .order_by(ShuttleBatch.starts_at.desc())
        .limit(1)
    )


async def check_can_reopen(db: AsyncSession, batch: ShuttleBatch) -> None:
    """Raise ``ReopenRefused`` unless ``batch`` is the one in use and nothing waits."""
    current = await current_batch(db)
    if current is None or current.id != batch.id:
        raise ReopenRefused(f"Batch #{batch.id} isn't the one in use.")
    if (waiting := await waiting_batch(db)) is not None:
        raise ReopenRefused(waiting_refusal(waiting))


async def reopen(db: AsyncSession, batch: ShuttleBatch) -> ShuttleBatch | None:
    """Undo finishing the previous batch: ``batch``, the one in use, goes back to waiting
    and the batch before it is in use again. Returns that batch, if there is one."""
    await check_can_reopen(db, batch)
    previous = await previous_batch(db, batch)
    try:
        async with _making_one_wait(db):
            batch.starts_at = None
            batch.opened_at = None
    except AlreadyWaiting as refusal:  # a purchase was recorded since the check
        await db.refresh(batch)  # the savepoint's rollback expired it
        raise ReopenRefused(str(refusal)) from None
    return previous


async def split_as_of(db: AsyncSession, starts_at: datetime, now: datetime) -> datetime:
    """When the split of the batch closed by one starting at ``starts_at`` is complete:
    ``now``, or the end of a session still in progress that the closed batch covers."""
    last_end = await db.scalar(
        select(func.max(Session.ends_at)).where(
            Session.status == "scheduled", Session.starts_at < starts_at
        )
    )
    return max(now, last_end) if last_end is not None else now


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


async def member_names(db: AsyncSession) -> dict[int, str]:
    return {m.id: m.display_name for m in await db.scalars(select(Member))}


def describe(batch: ShuttleBatch, names: dict[int, str]) -> str:
    """``#13 ($76.79, paid by yt)``, for messages (HTML)."""
    payer = html.escape(names[batch.payer_member_id])
    return f"#{batch.id} ({fmt_sgd(batch.amount_cents)}, paid by {payer})"


async def split_lines(
    db: AsyncSession,
    batch: ShuttleBatch,
    names: dict[int, str],
    now: datetime,
    *,
    heading: str,
    footer: str = "These amounts go on the next monthly invoice.",
) -> list[str]:
    """How a finished batch is split (FR-SH-3), under ``heading``, with ``footer`` when
    someone is charged. A session still in progress that the batch covers counts as played."""
    successor_start = await next_start(db, batch)
    as_of = await split_as_of(db, successor_start, now) if successor_start else now
    split = closed_batch(await load_ledger(db, as_of), batch.id)
    if split is None or not split.shares:
        payer = html.escape(names[batch.payer_member_id])
        return [f"{heading}: nobody played with it, so it stays with {payer}."]
    per_hour = fmt_sgd(round(split.amount_cents * 60 / split.minutes))
    lines = [
        f"{heading}: {fmt_sgd(split.amount_cents)} over {fmt_hours(split.minutes)} = {per_hour}/h"
    ]
    lines += [
        f"• {html.escape(names[member_id])} {fmt_hours(minutes)} — {fmt_sgd(cents)}"
        for member_id, minutes, cents in split.shares
    ]
    lines.append(footer)
    return lines
