"""[✅ … paid] buttons on the invoice card (FR-INV-4)."""

from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from badminton.bot.context import db_session, settings_of
from badminton.db.models import Invoice, InvoiceLine, Member
from badminton.services import audit
from badminton.services.invoice import previous_invoice, refresh_invoice_card, toggle_paid
from badminton.services.members import upsert_from_telegram

REPLACED = "This invoice has been replaced by a newer one."


async def on_paid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    line_id = int(query.data.split(":")[1])
    async with db_session(context) as db:
        line = await db.get(InvoiceLine, line_id)
        latest = await previous_invoice(db)
        if line is None or latest is None or line.invoice_id != latest.id or line.carried_at:
            await query.answer(REPLACED, show_alert=True)
            return
        payer = await db.get(Member, line.from_member_id)
        payee = await db.get(Member, line.to_member_id)
        user_id = update.effective_user.id
        allowed = {payer.telegram_user_id, payee.telegram_user_id} - {None}
        if user_id not in allowed and not settings_of(context).is_admin(user_id):
            await query.answer(
                f"Only {payer.display_name}, {payee.display_name} or the admin can mark this.",
                show_alert=True,
            )
            return
        actor = await upsert_from_telegram(db, update.effective_user)
        paid = await toggle_paid(db, line, actor.id)
        audit.record(
            db,
            actor_member_id=actor.id,
            action="invoice.paid" if paid else "invoice.unpaid",
            invoice_line_id=line.id,
            amount_cents=line.amount_cents,
        )
        await db.commit()
        invoice = await db.get(Invoice, line.invoice_id)
        await query.answer("Marked as paid ✅" if paid else "Marked as unpaid")
        await refresh_invoice_card(context.bot, db, invoice)


def register(application: Application) -> None:
    application.add_handler(CallbackQueryHandler(on_paid, pattern=r"^inv:\d+$"))
