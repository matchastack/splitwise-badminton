"""Attendance and the attendance card (FR-ATT-2, FR-ATT-3, FR-ATT-5).

The card names people by display name only: no @mentions, so nobody is pinged
(FR-ATT-4). Members who never answer count as out.
"""

from __future__ import annotations

import contextlib
import html
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.error import BadRequest

from badminton.config import Settings
from badminton.db.models import Attendance, Member, Session
from badminton.domain.money import fmt_sgd
from badminton.domain.timeutil import fmt_hours, fmt_session, is_card_due
from badminton.services.tick import TickContext

IN = "in"
OUT = "out"


@dataclass(frozen=True)
class CardView:
    session: Session
    payments: list[tuple[str, int]]  # (payer name, cents) per active booking
    ins: list[str]
    outs: list[str]


async def load_card_view(db: AsyncSession, session_id: int) -> CardView:
    session = await db.scalar(
        select(Session)
        .where(Session.id == session_id)
        .options(selectinload(Session.bookings), selectinload(Session.attendance))
        .execution_options(populate_existing=True)
    )
    member_ids = {b.payer_member_id for b in session.bookings} | {
        a.member_id for a in session.attendance
    }
    names = {
        m.id: m.display_name
        for m in await db.scalars(select(Member).where(Member.id.in_(member_ids)))
    }
    payments = [
        (names[b.payer_member_id], b.amount_cents)
        for b in sorted(session.bookings, key=lambda b: b.id)
        if b.cancelled_at is None
    ]

    def answered(status: str) -> list[str]:
        chosen = [names[a.member_id] for a in session.attendance if a.status == status]
        return sorted(chosen, key=str.casefold)

    return CardView(session, payments, answered(IN), answered(OUT))


def render_card(view: CardView, *, locked: bool = False) -> tuple[str, InlineKeyboardMarkup | None]:
    session = view.session
    minutes = int((session.ends_at - session.starts_at).total_seconds() // 60)
    total = sum(cents for _, cents in view.payments)
    if len(view.payments) == 1:
        paid = f"paid by {html.escape(view.payments[0][0])}"
    else:
        paid = ", ".join(f"{html.escape(name)} {fmt_sgd(cents)}" for name, cents in view.payments)
    lines = [
        f"🏸 <b>{fmt_session(session.starts_at, session.ends_at)}</b> ({fmt_hours(minutes)})",
        f"💵 Court {fmt_sgd(total)} — {paid}",
        f"✅ In ({len(view.ins)}): {_names(view.ins)}",
        f"❌ Out ({len(view.outs)}): {_names(view.outs)}",
    ]
    if locked:
        lines.append("🔒 Attendance is locked.")
        return "\n".join(lines), None
    lines.append("No answer = not playing.")
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ I'm in", callback_data=f"att:{IN}:{session.id}"),
                InlineKeyboardButton("❌ I'm out", callback_data=f"att:{OUT}:{session.id}"),
            ]
        ]
    )
    return "\n".join(lines), markup


def _names(names: list[str]) -> str:
    return ", ".join(html.escape(name) for name in names) if names else "—"


async def set_attendance(db: AsyncSession, session_id: int, member_id: int, status: str) -> None:
    statement = insert(Attendance).values(session_id=session_id, member_id=member_id, status=status)
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=[Attendance.session_id, Attendance.member_id],
            set_={"status": status, "updated_at": func.now()},
        )
    )


async def post_card(
    bot: Bot, db: AsyncSession, session: Session, chat_id: int, now: datetime
) -> Message:
    """Send the session's attendance card and remember where it is."""
    text, markup = render_card(await load_card_view(db, session.id))
    message = await bot.send_message(chat_id, text, reply_markup=markup)
    session.card_chat_id = chat_id
    session.card_message_id = message.message_id
    session.card_sent_at = now
    await db.flush()
    return message


async def refresh_card(
    bot: Bot, db: AsyncSession, session_id: int, *, locked: bool = False
) -> None:
    """Re-render a posted card after its session changed (bookings, attendance, edits)."""
    view = await load_card_view(db, session_id)
    session = view.session
    if session.card_message_id is None or session.status == "cancelled":
        return
    text, markup = render_card(view, locked=locked)
    with contextlib.suppress(BadRequest):  # unchanged, deleted or too old to edit
        await bot.edit_message_text(
            text,
            chat_id=session.card_chat_id,
            message_id=session.card_message_id,
            reply_markup=markup,
        )


async def close_cancelled_card(bot: Bot, session: Session) -> None:
    """Close a cancelled session's attendance card, if one was posted."""
    if session.status != "cancelled" or session.card_message_id is None:
        return
    with contextlib.suppress(BadRequest):  # the card was deleted or can't be edited
        await bot.edit_message_text(
            f"❌ <b>Session cancelled</b>: {fmt_session(session.starts_at, session.ends_at)}",
            chat_id=session.card_chat_id,
            message_id=session.card_message_id,
        )


async def post_card_if_due(
    bot: Bot, db: AsyncSession, session: Session, settings: Settings, now: datetime
) -> bool:
    """Post the session's card if it is due and not yet posted (FR-ATT-1, FR-BK-11).

    The card is claimed with an atomic update first, so two ticks running at once
    can't both post it. If sending fails the claim is released for the next run.
    Commits the session.
    """
    if settings.group_chat_id is None or session.status != "scheduled" or session.ends_at <= now:
        return False
    if not is_card_due(session.starts_at, now, settings.reminder_days_before):
        return False
    session_id = session.id
    claimed = await db.scalar(
        update(Session)
        .where(Session.id == session_id, Session.card_sent_at.is_(None))
        .values(card_sent_at=now)
        .returning(Session.id)
    )
    await db.commit()
    if claimed is None:
        return False
    try:
        await post_card(bot, db, session, settings.group_chat_id, now)
    except Exception:
        await db.rollback()
        await db.execute(update(Session).where(Session.id == session_id).values(card_sent_at=None))
        await db.commit()
        raise
    await db.commit()
    return True


async def post_due_cards(context: TickContext) -> None:
    """Tick step: post every card that is due, catching up on missed days."""
    async with context.sessionmaker() as db:
        candidates = await db.scalars(
            select(Session)
            .where(
                Session.status == "scheduled",
                Session.card_sent_at.is_(None),
                Session.ends_at > context.now,
            )
            .order_by(Session.starts_at, Session.id)
        )
        for session in candidates.all():
            await post_card_if_due(context.bot, db, session, context.settings, context.now)
