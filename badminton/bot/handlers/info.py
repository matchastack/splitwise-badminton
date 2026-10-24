"""/balance and /upcoming (FR-LED-8, FR-LED-9)."""

from __future__ import annotations

import html
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from badminton.bot.context import db_session
from badminton.db.models import Member, Session
from badminton.domain.ledger import Ledger
from badminton.domain.money import fmt_sgd
from badminton.domain.timeutil import fmt_hours, fmt_session, utc_now
from badminton.services.invoice import LineView, open_lines_for
from badminton.services.members import mention_html, upsert_from_telegram
from badminton.services.repo import load_ledger
from badminton.services.shuttle import describe, member_names, waiting_batch
from badminton.services.statements import balance_phrase


def balance_lines(ledger: Ledger, member: Member) -> list[str]:
    status = balance_phrase(ledger.balance(member.id))
    lines = [f"💰 <b>{html.escape(member.display_name)}</b>, {status}."]
    pending = ledger.pending_minutes(member.id)
    if ledger.open_batch_id is not None:
        played = fmt_hours(pending) if pending else "no time"
        lines.append(
            f"🏸 Current shuttle batch #{ledger.open_batch_id}: {played} played so far. "
            "It's billed once the batch is finished."
        )
    return lines


async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with db_session(context) as db:
        member = await upsert_from_telegram(db, update.effective_user)
        await db.commit()
        ledger = await load_ledger(db, utc_now())
        lines = balance_lines(ledger, member)
        if (waiting := await waiting_batch(db)) is not None:
            when = "to be put in use" if ledger.open_batch_id is None else "to take over"
            lines.append(f"🛍 Batch {describe(waiting, await member_names(db))} is waiting {when}.")
        lines += unpaid_lines(member, await open_lines_for(db, member.id))
    await update.effective_message.reply_text("\n".join(lines))


def unpaid_lines(member: Member, views: list[LineView]) -> list[str]:
    """The caller's unpaid lines on the latest invoice (FR-LED-8)."""
    lines = []
    for view in views:
        amount = fmt_sgd(view.line.amount_cents)
        if view.payer.id == member.id:
            lines.append(f"🧾 To settle: pay {mention_html(view.payee)} {amount}")
        else:
            lines.append(f"🧾 To settle: {mention_html(view.payer)} owes you {amount}")
    return lines


async def upcoming_lines(db: AsyncSession, now: datetime) -> list[str]:
    sessions = (
        await db.scalars(
            select(Session)
            .where(Session.status == "scheduled", Session.ends_at > now)
            .options(selectinload(Session.bookings))
            .order_by(Session.starts_at)
        )
    ).all()
    if not sessions:
        return ["No sessions booked yet. Send /book in the group to record one."]
    names = {m.id: m.display_name for m in await db.scalars(select(Member))}
    lines = ["📅 <b>Upcoming sessions</b>"]
    for session in sessions:
        payers = sorted(
            {names[b.payer_member_id] for b in session.bookings if b.cancelled_at is None},
            key=str.casefold,
        )
        lines.append(
            f"• {fmt_session(session.starts_at, session.ends_at)} ({fmt_hours(session.minutes)}) — "
            f"booked by {html.escape(', '.join(payers))}"
        )
    return lines


async def show_upcoming(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with db_session(context) as db:
        lines = await upcoming_lines(db, utc_now())
    await update.effective_message.reply_text("\n".join(lines))


def register(application: Application) -> None:
    application.add_handler(CommandHandler("balance", show_balance))
    application.add_handler(CommandHandler("upcoming", show_upcoming))
