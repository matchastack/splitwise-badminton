"""Locking attendance when a session ends (FR-ATT-5 to FR-ATT-7).

Finalising happens exactly once per session, triggered by the tick or by a tap
after the end: the card loses its buttons and a summary is posted.
"""

from __future__ import annotations

import html
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot

from badminton.config import Settings
from badminton.db.models import Session, ShuttleBatch
from badminton.domain.ledger import BatchFact, batch_of
from badminton.domain.money import allocate, fmt_sgd
from badminton.domain.timeutil import fmt_hours, fmt_session
from badminton.services.attendance import CardView, load_card_view, refresh_card
from badminton.services.notify import announce, dm_admins
from badminton.services.tick import TickContext


async def finalize_session(
    bot: Bot, db: AsyncSession, session_id: int, settings: Settings, now: datetime
) -> bool:
    """Lock an ended session's attendance and post its summary, once. Commits.

    If the summary can't be posted, the lock is released so the next run tries again.
    Once it is posted the session stays locked, even if the admin can't be told.
    """
    claimed = await db.scalar(
        update(Session)
        .where(
            Session.id == session_id,
            Session.locked_at.is_(None),
            Session.status == "scheduled",
            Session.ends_at <= now,
        )
        .values(locked_at=now)
        .returning(Session.id)
    )
    await db.commit()
    if claimed is None:
        return False
    try:
        await refresh_card(bot, db, session_id, locked=True)
        view = await load_card_view(db, session_id)
        await announce(bot, settings, await summary(db, view))
    except Exception:
        await db.rollback()
        await db.execute(update(Session).where(Session.id == session_id).values(locked_at=None))
        await db.commit()
        raise
    if not view.ins:
        when = fmt_session(view.session.starts_at, view.session.ends_at)
        await dm_admins(
            bot,
            settings,
            f"⚠️ Nobody was marked in for <b>{when}</b>, so it's left out of the costs. "
            "Use /edit to fix the attendance or cancel the session.",
        )
    return True


async def summary(db: AsyncSession, view: CardView) -> str:
    session = view.session
    title = f"🔒 <b>{fmt_session(session.starts_at, session.ends_at)}</b> is locked."
    if not view.ins:
        return f"{title}\nNobody was marked in, so the admin has been asked to check it."

    minutes = int((session.ends_at - session.starts_at).total_seconds() // 60)
    court = sum(cents for _, cents in view.payments)
    shares = allocate(court, dict.fromkeys(range(len(view.ins)), 1)).values()
    each = fmt_sgd(min(shares))
    if max(shares) != min(shares):
        each = f"{fmt_sgd(min(shares))}–{fmt_sgd(max(shares))}"
    names = ", ".join(html.escape(name) for name in view.ins)
    lines = [title, f"👥 {len(view.ins)} played: {names}", f"💵 Court {each} each"]

    batches = [
        BatchFact(b.id, b.payer_member_id, b.amount_cents, b.starts_at, b.created_at)
        for b in await db.scalars(select(ShuttleBatch).order_by(ShuttleBatch.starts_at))
    ]
    if (batch := batch_of(session.starts_at, batches)) is not None:
        lines.append(f"🏸 +{fmt_hours(minutes)} each on shuttle batch #{batch.id}")
    return "\n".join(lines)


async def finalize_ended_sessions(context: TickContext) -> None:
    """Tick step: finalise every session that has ended but isn't locked yet."""
    async with context.sessionmaker() as db:
        ended = await db.scalars(
            select(Session.id)
            .where(
                Session.status == "scheduled",
                Session.locked_at.is_(None),
                Session.ends_at <= context.now,
            )
            .order_by(Session.ends_at, Session.id)
        )
        for session_id in ended.all():
            await finalize_session(context.bot, db, session_id, context.settings, context.now)
