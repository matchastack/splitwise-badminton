"""Sessions and bookings.

A session is one block of play: one or more bookings on the same day that overlap
or run back-to-back, e.g. two people each booking an hour (FR-BK-7). Its start and
end span its bookings.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from badminton.db.models import Booking, Session
from badminton.domain.timeutil import sgt_date


async def find_mergeable_session(
    db: AsyncSession, starts_at: datetime, ends_at: datetime
) -> Session | None:
    """A scheduled, unlocked session on the same SGT date that overlaps or touches
    ``starts_at``–``ends_at``; the earliest if several do."""
    candidates = await db.scalars(
        select(Session)
        .where(
            Session.status == "scheduled",
            Session.locked_at.is_(None),
            Session.starts_at <= ends_at,
            Session.ends_at >= starts_at,
        )
        .order_by(Session.starts_at, Session.id)
    )
    day = sgt_date(starts_at)
    return next((s for s in candidates if sgt_date(s.starts_at) == day), None)


async def record_booking(
    db: AsyncSession,
    *,
    payer_id: int,
    starts_at: datetime,
    minutes: int,
    amount_cents: int,
    photo_file_id: str | None,
    record_chat_id: int | None,
    record_message_id: int | None,
    session_id: int | None = None,
    venue: str | None = None,
    courts: str | None = None,
) -> Booking:
    """Store a booking, in ``session_id`` if given, otherwise in a new session.
    The submitter is the payer (FR-BK-1)."""
    ends_at = starts_at + timedelta(minutes=minutes)
    if session_id is None:
        session = Session(starts_at=starts_at, ends_at=ends_at)
        db.add(session)
        await db.flush()
        session_id = session.id
    booking = Booking(
        session_id=session_id,
        payer_member_id=payer_id,
        submitted_by_member_id=payer_id,
        starts_at=starts_at,
        minutes=minutes,
        amount_cents=amount_cents,
        photo_file_id=photo_file_id,
        venue=venue,
        courts=courts,
        record_chat_id=record_chat_id,
        record_message_id=record_message_id,
    )
    db.add(booking)
    await db.flush()
    await recompute_span(db, session_id)
    return booking


async def recompute_span(db: AsyncSession, session_id: int) -> Session:
    """Set the session's start and end from its bookings that aren't cancelled."""
    session = await db.scalar(
        select(Session)
        .where(Session.id == session_id)
        .options(selectinload(Session.bookings))
        .execution_options(populate_existing=True)
    )
    active = [booking for booking in session.bookings if booking.cancelled_at is None]
    if active:
        session.starts_at = min(booking.starts_at for booking in active)
        session.ends_at = max(
            booking.starts_at + timedelta(minutes=booking.minutes) for booking in active
        )
    await db.flush()
    return session


async def cancel_booking(db: AsyncSession, booking: Booking, now: datetime) -> Session:
    """Cancel a booking (FR-BK-10). A session left with no bookings is cancelled too."""
    booking.cancelled_at = now
    await db.flush()
    session = await recompute_span(db, booking.session_id)
    if all(b.cancelled_at is not None for b in session.bookings):
        session.status = "cancelled"
        await db.flush()
    return session


async def cancel_session(db: AsyncSession, session_id: int, now: datetime) -> Session:
    """Cancel a session and all its bookings (FR-ADM-2)."""
    session = await recompute_span(db, session_id)
    for booking in session.bookings:
        if booking.cancelled_at is None:
            booking.cancelled_at = now
    session.status = "cancelled"
    await db.flush()
    return session
