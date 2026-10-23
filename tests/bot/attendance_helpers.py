"""Helpers for sessions with posted attendance cards."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from badminton.db.models import Booking, Member, Session
from badminton.domain.timeutil import utc_now
from badminton.services.attendance import post_card
from badminton.services.sessions import record_booking
from tests.conftest import BotHarness
from tests.fake_telegram import GROUP_ID


async def make_session(
    bot: BotHarness,
    *,
    starts_at: datetime | None = None,
    minutes: int = 120,
    amount_cents: int = 1480,
    payer_name: str = "alice",
    payer_telegram_id: int | None = 111,
) -> int:
    """A scheduled session with one booking; returns its ID."""
    starts_at = starts_at or utc_now() + timedelta(days=2)
    async with bot.sessionmaker() as db:
        payer = None
        if payer_telegram_id is not None:
            payer = await db.scalar(
                select(Member).where(Member.telegram_user_id == payer_telegram_id)
            )
        if payer is None:
            payer = Member(
                display_name=payer_name, telegram_user_id=payer_telegram_id, username="alice_handle"
            )
            db.add(payer)
            await db.flush()
        booking = await record_booking(
            db,
            payer_id=payer.id,
            starts_at=starts_at,
            minutes=minutes,
            amount_cents=amount_cents,
            photo_file_id=None,
            record_chat_id=None,
            record_message_id=None,
        )
        await db.commit()
        return booking.session_id


async def post(bot: BotHarness, session_id: int) -> dict[str, Any]:
    """Post the session's card; returns the card message."""
    async with bot.sessionmaker() as db:
        session = await db.get(Session, session_id)
        message = await post_card(bot.application.bot, db, session, GROUP_ID, utc_now())
        await db.commit()
    return bot.telegram.last("sendMessage").message | {"message_id": message.message_id}


async def move_session(
    bot: BotHarness, session_id: int, starts_at: datetime, minutes: int = 120
) -> None:
    async with bot.sessionmaker() as db:
        await db.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(starts_at=starts_at, ends_at=starts_at + timedelta(minutes=minutes))
        )
        await db.execute(
            update(Booking).where(Booking.session_id == session_id).values(starts_at=starts_at)
        )
        await db.commit()
