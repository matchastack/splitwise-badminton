"""Paying an invoice line (FR-INV-4).

The payer taps [✅ … paid] on the group card. The line is then pending, and the payee is
asked by DM to confirm they've received it; they can also confirm on the card. A tap by
the payee or the admin marks it paid straight away.
"""

from __future__ import annotations

import contextlib

from telegram import CallbackQuery, Update
from telegram.constants import ChatType
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from badminton.bot.admin import admin_only
from badminton.bot.context import db_session, settings_of
from badminton.bot.membership import private_only
from badminton.db.models import Invoice, InvoiceLine, Member
from badminton.domain.money import fmt_sgd
from badminton.domain.timeutil import fmt_period, utc_now
from badminton.services import audit
from badminton.services.invoice import (
    NOT_RECEIVED,
    PAID,
    PENDING,
    RECEIVED,
    ask_payee,
    confirm_paid,
    confirm_request,
    line_state,
    mark_unpaid,
    previous_invoice,
    refresh_invoice_card,
)
from badminton.services.members import mention_html, upsert_from_telegram
from badminton.services.monthly import manual_period, run_invoice
from badminton.services.notify import dm

REPLACED = "This invoice has been replaced by a newer one."
ALREADY_PAID = "This payment is already confirmed ✅"
NOT_PENDING = "This payment is no longer waiting for confirmation."

CLAIM = "claim"
CONFIRM = "confirm"
UNCLAIM = "unclaim"
UNDO = "undo"


async def on_paid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, line_id, *action = query.data.split(":")
    in_dm = update.effective_chat.type == ChatType.PRIVATE
    async with db_session(context) as db:
        # Locked, so a double tap, or the payee and the admin confirming at once, is
        # handled one tap at a time.
        line = await db.get(InvoiceLine, int(line_id), with_for_update=True)
        latest = await previous_invoice(db)
        if line is None or latest is None or line.invoice_id != latest.id or line.carried_at:
            await _stale(query, REPLACED, in_dm)
            return
        payer = await db.get(Member, line.from_member_id)
        payee = await db.get(Member, line.to_member_id)
        user_id = update.effective_user.id
        is_admin = settings_of(context).is_admin(user_id)
        allowed = {payer.telegram_user_id, payee.telegram_user_id} - {None}
        if user_id not in allowed and not is_admin:
            await query.answer(
                f"Only {payer.display_name}, {payee.display_name} or the admin can mark this.",
                show_alert=True,
            )
            return
        state = await line_state(db, line)
        is_payer = user_id == payer.telegram_user_id
        may_confirm = user_id == payee.telegram_user_id or is_admin

        if not action:  # ✅ paid, or ↩️ undo on a paid line
            if state == PAID:
                outcome = UNDO
            elif is_payer and state == PENDING:
                await query.answer(f"Waiting for {payee.display_name} to confirm ⏳")
                return
            else:
                outcome = CLAIM if is_payer else CONFIRM
        elif state != PENDING:
            await _stale(query, ALREADY_PAID if state == PAID else NOT_PENDING, in_dm)
            return
        elif action[0] == RECEIVED:
            if not may_confirm:
                await query.answer(
                    f"Only {payee.display_name} or the admin can confirm this.", show_alert=True
                )
                return
            outcome = CONFIRM
        else:
            outcome = UNCLAIM

        actor = await upsert_from_telegram(db, update.effective_user)
        if outcome == CLAIM:
            line.claimed_at = utc_now()
            action_name = "invoice.claimed"
        elif outcome == CONFIRM:
            await confirm_paid(db, line, actor.id)
            action_name = "invoice.paid"
        else:
            await mark_unpaid(db, line)
            action_name = "invoice.unclaimed" if outcome == UNCLAIM else "invoice.unpaid"
        audit.record(
            db,
            actor_member_id=actor.id,
            action=action_name,
            invoice_line_id=line.id,
            amount_cents=line.amount_cents,
        )
        await db.commit()

        payee_said_no = outcome == UNCLAIM and actor.id == payee.id
        if outcome == CLAIM:
            await query.answer(f"Waiting for {payee.display_name} to confirm ⏳")
        elif outcome == CONFIRM:
            await query.answer("Marked as paid ✅")
        else:
            await query.answer("Marked as not received" if payee_said_no else "Marked as unpaid")
        invoice = await db.get(Invoice, line.invoice_id)
        if in_dm:
            result = "✅ Confirmed. Thanks!" if outcome == CONFIRM else "❌ Not received."
            with contextlib.suppress(BadRequest):
                await query.edit_message_text(
                    f"{confirm_request(invoice, line, payer)}\n\n{result}"
                )
        await refresh_invoice_card(context.bot, db, invoice)
        if outcome == CLAIM:
            await ask_payee(context.bot, invoice, line, payer, payee)
        elif payee_said_no:
            await dm(
                context.bot,
                payer,
                f"❌ {mention_html(payee)} hasn't received your {fmt_sgd(line.amount_cents)} "
                f"for {fmt_period(invoice.period)} yet, so it's unpaid again. "
                "Tap ✅ on the group card once you've paid.",
            )
        await db.commit()


async def _stale(query: CallbackQuery, text: str, in_dm: bool) -> None:
    """Answer a tap that no longer applies. In a DM, its buttons are removed too."""
    await query.answer(text, show_alert=True)
    if in_dm:
        with contextlib.suppress(BadRequest):
            await query.edit_message_reply_markup(None)


@private_only
@admin_only
async def invoice_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/invoice: post the settle-up now (FR-INV-1). Early in a month that's the previous
    month's, if the monthly run hasn't posted it yet."""
    now = utc_now()
    async with db_session(context) as db:
        period = await manual_period(db, now)
        invoice = await run_invoice(context.bot, db, settings_of(context), period=period, now=now)
    message = update.effective_message
    if invoice is None:
        await message.reply_text(
            f"{fmt_period(period)} already has an invoice. "
            "New activity goes on next month's invoice."
        )
    else:
        await message.reply_text(f"🧾 Posted the {fmt_period(period)} invoice in the group.")


def register(application: Application) -> None:
    application.add_handler(CommandHandler("invoice", invoice_now))
    application.add_handler(
        CallbackQueryHandler(on_paid, pattern=rf"^inv:\d+(:({RECEIVED}|{NOT_RECEIVED}))?$")
    )
