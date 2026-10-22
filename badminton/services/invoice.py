"""Monthly invoices (FR-INV-1 to FR-INV-3, FR-INV-6, FR-INV-7).

An invoice is generated once per period. Its settle-up lines come from all-time
balances by fewest transfers; each member's closing balance is stored so the next
statement can show corrections as adjustments. When a new invoice is generated, the
previous one's unpaid lines are marked as carried into it.
"""

from __future__ import annotations

import contextlib
import html
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from badminton.config import Settings
from badminton.db.models import Invoice, InvoiceBalance, InvoiceLine, Member, Transfer
from badminton.domain.ledger import Ledger
from badminton.domain.money import fmt_sgd
from badminton.domain.settle import simplify_debts
from badminton.domain.statement import group_totals
from badminton.domain.timeutil import fmt_hours, fmt_period
from badminton.services.members import mention_html
from badminton.services.notify import announce
from badminton.services.repo import load_ledger

OPEN = "open"
PAID = "paid"
CARRIED = "carried"


async def previous_invoice(db: AsyncSession, before: Invoice | None = None) -> Invoice | None:
    query = select(Invoice).order_by(Invoice.created_at.desc(), Invoice.id.desc())
    if before is not None:
        query = query.where(Invoice.id != before.id, Invoice.created_at <= before.created_at)
    return await db.scalar(query.limit(1))


async def generate_invoice(db: AsyncSession, *, period: str, now: datetime) -> Invoice | None:
    """Create the invoice for ``period``, or return None if it already exists.

    The period is claimed with INSERT … ON CONFLICT DO NOTHING, so concurrent runs
    can't both create it.
    """
    previous = await previous_invoice(db)
    ledger = await load_ledger(db, now)
    invoice_id = await db.scalar(
        insert(Invoice)
        .values(period=period, created_at=now)
        .on_conflict_do_nothing(index_elements=[Invoice.period])
        .returning(Invoice.id)
    )
    if invoice_id is None:
        return None
    invoice = await db.get(Invoice, invoice_id)
    names = {m.id: m for m in await db.scalars(select(Member))}
    invoice.summary = _summary(period, ledger, previous, names)

    for payment in simplify_debts(ledger.balances):
        db.add(
            InvoiceLine(
                invoice_id=invoice.id,
                from_member_id=payment.from_id,
                to_member_id=payment.to_id,
                amount_cents=payment.amount_cents,
            )
        )
    for member_id, cents in ledger.balances.items():
        db.add(InvoiceBalance(invoice_id=invoice.id, member_id=member_id, balance_cents=cents))

    if previous is not None:
        for line in await unpaid_lines(db, previous):
            line.carried_at = now
    await db.flush()
    return invoice


async def discard_invoice(db: AsyncSession, invoice_id: int, previous_id: int | None) -> None:
    """Undo ``generate_invoice`` after its card couldn't be posted, so the period can be
    invoiced again. The previous invoice's lines are no longer carried."""
    if previous_id is not None:
        await db.execute(
            update(InvoiceLine).where(InvoiceLine.invoice_id == previous_id).values(carried_at=None)
        )
    await db.execute(delete(InvoiceLine).where(InvoiceLine.invoice_id == invoice_id))
    await db.execute(delete(InvoiceBalance).where(InvoiceBalance.invoice_id == invoice_id))
    await db.execute(delete(Invoice).where(Invoice.id == invoice_id))


def _summary(
    period: str, ledger: Ledger, previous: Invoice | None, members: dict[int, Member]
) -> str:
    totals = group_totals(ledger, since=previous.created_at if previous else None)
    count = len(totals.session_ids)
    sessions = f"{count} session{'s' if count != 1 else ''}"
    lines = [
        f"🧾 <b>{fmt_period(period)}</b>",
        f"🏸 {sessions} · court {fmt_sgd(totals.court_cents)}",
    ]
    for batch in totals.batches_closed:
        payer = html.escape(members[batch.payer_id].display_name)
        lines.append(
            f"🏸 Shuttle batch #{batch.batch_id} finished: {fmt_sgd(batch.amount_cents)} "
            f"over {fmt_hours(batch.minutes)}, bought by {payer}"
        )
    return "\n".join(lines)


async def unpaid_lines(db: AsyncSession, invoice: Invoice) -> list[InvoiceLine]:
    paid = select(Transfer.invoice_line_id).where(Transfer.invoice_line_id.is_not(None))
    lines = await db.scalars(
        select(InvoiceLine)
        .where(
            InvoiceLine.invoice_id == invoice.id,
            InvoiceLine.carried_at.is_(None),
            InvoiceLine.id.not_in(paid),
        )
        .order_by(InvoiceLine.id)
    )
    return list(lines)


@dataclass(frozen=True)
class LineView:
    line: InvoiceLine
    payer: Member
    payee: Member
    state: str


async def line_views(db: AsyncSession, invoice: Invoice) -> list[LineView]:
    members = {m.id: m for m in await db.scalars(select(Member))}
    paid_ids = set(
        await db.scalars(
            select(Transfer.invoice_line_id)
            .join(InvoiceLine, InvoiceLine.id == Transfer.invoice_line_id)
            .where(InvoiceLine.invoice_id == invoice.id)
        )
    )
    lines = await db.scalars(
        select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id).order_by(InvoiceLine.id)
    )
    views = []
    for line in lines:
        if line.id in paid_ids:
            state = PAID
        elif line.carried_at is not None:
            state = CARRIED
        else:
            state = OPEN
        views.append(
            LineView(line, members[line.from_member_id], members[line.to_member_id], state)
        )
    return views


def render_invoice(
    invoice: Invoice,
    views: list[LineView],
    carried_into: str | None = None,
    *,
    interactive: bool = True,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """The group card: the frozen summary plus each settle-up line's current state.

    On the latest invoice each line not carried over has a button to mark it paid,
    or to undo that (FR-INV-4). Replaced invoices have no buttons.
    """
    lines = [invoice.summary, ""]
    if not views:
        lines.append("Everyone is square 🎉")
        return "\n".join(lines), None
    lines.append("<b>Settle up</b>")
    buttons = []
    if interactive:
        for view in views:
            names = f"{view.payer.display_name} → {view.payee.display_name}"
            if view.state == OPEN:
                label = f"✅ {names} {fmt_sgd(view.line.amount_cents)} paid"
            elif view.state == PAID:
                label = f"↩️ Undo {names}"
            else:
                continue
            buttons.append([InlineKeyboardButton(label, callback_data=f"inv:{view.line.id}")])
    for view in views:
        transfer = (
            f"{mention_html(view.payer)} → {mention_html(view.payee)} "
            f"{fmt_sgd(view.line.amount_cents)}"
        )
        if view.state == PAID:
            lines.append(f"✅ <s>{transfer}</s>")
        elif view.state == CARRIED:
            where = f" into {html.escape(carried_into)}" if carried_into else ""
            lines.append(f"↪️ {transfer} (carried{where})")
        else:
            lines.append(f"• {transfer}")
    if buttons:
        lines += ["", "Tap ✅ once you've paid."]
    return "\n".join(lines), InlineKeyboardMarkup(buttons) if buttons else None


async def publish_invoice(bot: Bot, db: AsyncSession, settings: Settings, invoice: Invoice) -> None:
    """Post the invoice card to the group and remember where it is."""
    text, markup = render_invoice(invoice, await line_views(db, invoice))
    message = await announce(bot, settings, text, reply_markup=markup)
    if message is not None:
        invoice.card_chat_id = message.chat_id
        invoice.card_message_id = message.message_id
        await db.flush()


async def refresh_invoice_card(
    bot: Bot, db: AsyncSession, invoice: Invoice, carried_into: str | None = None
) -> None:
    if invoice.card_message_id is None:
        return
    latest = await previous_invoice(db)
    interactive = latest is not None and latest.id == invoice.id
    views = await line_views(db, invoice)
    text, markup = render_invoice(invoice, views, carried_into, interactive=interactive)
    with contextlib.suppress(BadRequest):  # unchanged or deleted
        await bot.edit_message_text(
            text,
            chat_id=invoice.card_chat_id,
            message_id=invoice.card_message_id,
            reply_markup=markup,
        )


async def toggle_paid(db: AsyncSession, line: InvoiceLine, recorded_by_member_id: int) -> bool:
    """Record the line's transfer, or remove it if already recorded. Returns True if
    the line is now paid."""
    existing = await db.scalar(select(Transfer).where(Transfer.invoice_line_id == line.id))
    if existing is not None:
        await db.delete(existing)
        await db.flush()
        return False
    db.add(
        Transfer(
            from_member_id=line.from_member_id,
            to_member_id=line.to_member_id,
            amount_cents=line.amount_cents,
            invoice_line_id=line.id,
            recorded_by_member_id=recorded_by_member_id,
        )
    )
    await db.flush()
    return True


async def open_lines_for(db: AsyncSession, member_id: int) -> list[LineView]:
    """Unpaid lines on the latest invoice that involve ``member_id``."""
    latest = await previous_invoice(db)
    if latest is None:
        return []
    return [
        view
        for view in await line_views(db, latest)
        if view.state == OPEN and member_id in (view.payer.id, view.payee.id)
    ]
