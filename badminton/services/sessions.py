"""Sessions and bookings."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from badminton.db.models import Booking, Session


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
) -> Booking:
    """Store a booking in a new session of its own. The submitter is the payer (FR-BK-1)."""
    session = Session(starts_at=starts_at, ends_at=starts_at + timedelta(minutes=minutes))
    db.add(session)
    await db.flush()
    booking = Booking(
        session_id=session.id,
        payer_member_id=payer_id,
        submitted_by_member_id=payer_id,
        starts_at=starts_at,
        minutes=minutes,
        amount_cents=amount_cents,
        photo_file_id=photo_file_id,
        record_chat_id=record_chat_id,
        record_message_id=record_message_id,
    )
    db.add(booking)
    await db.flush()
    return booking
