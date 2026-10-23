"""Personal monthly statements by DM (FR-INV-5, FR-INV-7, FR-MEM-3)."""

from __future__ import annotations

import html

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot

from badminton.db.models import Invoice, InvoiceBalance, Member
from badminton.domain.ledger import Entry, EntryKind
from badminton.domain.money import fmt_sgd, fmt_signed
from badminton.domain.statement import Statement, build_statement
from badminton.domain.timeutil import fmt_date, fmt_hours, fmt_period, sgt_date
from badminton.services.invoice import LineView, line_views, previous_invoice
from badminton.services.members import mention_html
from badminton.services.notify import dm
from badminton.services.repo import load_ledger


def balance_phrase(cents: int) -> str:
    if cents > 0:
        return f"you're owed {fmt_sgd(cents)}"
    if cents < 0:
        return f"you owe {fmt_sgd(-cents)}"
    return "you're all square"


def render_statement(
    period: str,
    statement: Statement,
    members: dict[int, Member],
    lines: list[LineView],
) -> str:
    out = [f"🧾 <b>Your {fmt_period(period)} statement</b>", ""]
    out.append(f"Opening balance: {fmt_signed(statement.reported_opening_cents)}")
    if statement.adjustment_cents:
        out.append(f"Corrections to earlier months: {fmt_signed(statement.adjustment_cents)}")
    out += [_entry_line(entry, members) for entry in statement.entries]
    out.append(f"<b>Closing balance</b>: {balance_phrase(statement.closing_cents)}.")
    out.append("")
    mine = [view for view in lines if statement.member_id in (view.payer.id, view.payee.id)]
    if not mine:
        out.append("Nothing to pay this month. 🎉")
    for view in mine:
        amount = fmt_sgd(view.line.amount_cents)
        if view.payer.id == statement.member_id:
            out.append(
                f"👉 Pay {mention_html(view.payee)} {amount}, then tap ✅ on the group card."
            )
        else:
            out.append(f"👉 {mention_html(view.payer)} will pay you {amount}.")
    return "\n".join(out)


def _entry_line(entry: Entry, members: dict[int, Member]) -> str:
    amount = fmt_signed(entry.amount_cents)
    when = fmt_date(sgt_date(entry.at))
    if entry.kind is EntryKind.COURT_SHARE:
        return f"🏸 {when} ({fmt_hours(entry.minutes or 0)}): court {amount}"
    if entry.kind is EntryKind.COURT_PAID:
        return f"💵 You paid for the court ({when}): {amount}"
    if entry.kind is EntryKind.SHUTTLE_SHARE:
        return f"🏸 Shuttle batch #{entry.batch_id} ({fmt_hours(entry.minutes or 0)}): {amount}"
    if entry.kind is EntryKind.SHUTTLE_PAID:
        return f"💵 You bought shuttle batch #{entry.batch_id}: {amount}"
    if entry.kind is EntryKind.OPENING_BALANCE:
        return f"📒 Carried over from the spreadsheet: {amount}"
    other = html.escape(members[entry.counterparty_id].display_name)
    if entry.kind is EntryKind.TRANSFER_SENT:
        return f"↔️ You paid {other} ({when}): {amount}"
    return f"↔️ {other} paid you ({when}): {amount}"


async def send_statements(bot: Bot, db: AsyncSession, invoice: Invoice) -> int:
    """DM each member who has /start-ed the bot and has something to report.
    Returns how many were sent. The caller commits (blocked users get DMs turned off)."""
    previous = await previous_invoice(db, before=invoice)
    previous_balances: dict[int, int] = {}
    if previous is not None:
        rows = await db.scalars(
            select(InvoiceBalance).where(InvoiceBalance.invoice_id == previous.id)
        )
        previous_balances = {row.member_id: row.balance_cents for row in rows}
    ledger = await load_ledger(db, invoice.created_at)
    members = {m.id: m for m in await db.scalars(select(Member).order_by(Member.id))}
    lines = await line_views(db, invoice)

    sent = 0
    for member in members.values():
        if not member.dm_enabled or member.telegram_user_id is None:
            continue
        statement = build_statement(
            member.id,
            ledger,
            since=previous.created_at if previous else None,
            previous_closing_cents=previous_balances.get(member.id, 0) if previous else None,
        )
        if not statement.has_activity and statement.closing_cents == 0:
            continue
        text = render_statement(invoice.period, statement, members, lines)
        if await dm(bot, member, text):
            sent += 1
    return sent
