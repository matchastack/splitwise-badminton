"""The attendance reminder (FR-ATT-4).

``REMINDER_DAYS_BEFORE`` days before a session, from 10:00 SGT, the bot replies to its
attendance card tagging the members who haven't answered. It's the only attendance
message that pings anyone, and it's sent once per session.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot, ReplyParameters

from badminton.config import Settings
from badminton.db.models import Attendance, Member, Session
from badminton.domain.timeutil import (
    fmt_session,
    is_daily_post_time,
    is_reminder_due,
    reminder_date,
    sgt_date,
)
from badminton.services.members import mention_html
from badminton.services.tick import TickContext, for_each


async def unanswered(db: AsyncSession, session_id: int) -> list[Member]:
    """The members a reminder tags: active ones with a Telegram account (guests have none
    to tag) who haven't answered, by name."""
    answered = select(Attendance.member_id).where(Attendance.session_id == session_id)
    members = await db.scalars(
        select(Member).where(
            Member.active,
            Member.telegram_user_id.is_not(None),
            Member.id.not_in(answered),
        )
    )
    return sorted(members, key=lambda m: m.display_name.casefold())


def render_reminder(session: Session, members: list[Member]) -> str:
    return (
        f"🔔 <b>{fmt_session(session.starts_at, session.ends_at)}</b>: are you playing? "
        "Tap ✅ I'm in or ❌ I'm out on the card.\n" + ", ".join(mention_html(m) for m in members)
    )


def is_due(session: Session, settings: Settings, now: datetime) -> bool:
    """Whether the session's reminder should go out now. Its card must be up in the group,
    and must have gone up before the reminder day: a session booked later than that has
    a fresh card and gets no reminder."""
    days = settings.reminder_days_before
    return (
        settings.group_chat_id is not None
        and session.status == "scheduled"
        and session.starts_at > now
        and session.reminder_sent_at is None
        and session.card_message_id is not None
        and session.card_chat_id == settings.group_chat_id
        and is_reminder_due(session.starts_at, now, days)
        and sgt_date(session.card_sent_at) < reminder_date(session.starts_at, days)
    )


async def send_reminder_if_due(
    bot: Bot, db: AsyncSession, session: Session, settings: Settings, now: datetime
) -> bool:
    """Send the session's reminder, in reply to its card, if it's due. If everyone has
    answered, nothing is sent but the reminder still counts as done.

    The reminder is claimed with an atomic update first, so two ticks running at once
    can't both send it. If sending fails the claim is released for the next run.
    Commits the session.
    """
    if not is_due(session, settings, now):
        return False
    session_id = session.id
    claimed = await db.scalar(
        update(Session)
        .where(Session.id == session_id, Session.reminder_sent_at.is_(None))
        .values(reminder_sent_at=now)
        .returning(Session.id)
    )
    await db.commit()
    if claimed is None:
        return False
    try:
        members = await unanswered(db, session_id)
        if members:
            await bot.send_message(
                settings.group_chat_id,
                render_reminder(session, members),
                reply_parameters=ReplyParameters(
                    session.card_message_id, allow_sending_without_reply=True
                ),
            )
    except Exception:
        await db.rollback()
        await db.execute(
            update(Session).where(Session.id == session_id).values(reminder_sent_at=None)
        )
        await db.commit()
        raise
    return True


async def send_due_reminders(context: TickContext) -> None:
    """Tick step: send every reminder that is due, catching up on missed days. Reminders
    wait for 10:00 SGT, so one due today doesn't go out at midnight."""
    if not is_daily_post_time(context.now):
        return
    async with context.sessionmaker() as db:
        candidates = await db.scalars(
            select(Session.id)
            .where(
                Session.status == "scheduled",
                Session.reminder_sent_at.is_(None),
                Session.card_message_id.is_not(None),
                Session.starts_at > context.now,
            )
            .order_by(Session.starts_at, Session.id)
        )

        async def send(session_id: int) -> None:
            session = await db.get(Session, session_id)
            await send_reminder_if_due(context.bot, db, session, context.settings, context.now)

        await for_each(db, candidates.all(), send, "Sending the attendance reminder")
