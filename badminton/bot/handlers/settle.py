"""/settle: the admin records a payment made outside the bot (FR-ADM-4).

If it matches an open line on the latest invoice (same people and amount), that line
is marked paid too, so the invoice card stays accurate.
"""

from __future__ import annotations

import html

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from badminton.bot import prompts
from badminton.bot.admin import admin_only
from badminton.bot.context import db_session, settings_of
from badminton.bot.keyboards import decode, encode
from badminton.db.models import Draft, Invoice, Member, Transfer
from badminton.domain.money import fmt_sgd, parse_amount
from badminton.domain.timeutil import utc_now
from badminton.services import audit
from badminton.services.drafts import create_draft, delete_draft, get_draft, update_data
from badminton.services.invoice import open_lines_for, refresh_invoice_card
from badminton.services.members import active_members, upsert_from_telegram
from badminton.services.notify import announce

KIND = "settle"
CALLBACK_KIND = "st"
MAX_TRANSFER_CENTS = 500_000  # $5,000

FROM = "f"
TO = "t"
RECORD = "ok"
CANCEL = "x"


def _button(text: str, draft: Draft, action: str, value: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=encode(CALLBACK_KIND, draft.id, action, value))


async def render(db: AsyncSession, draft: Draft) -> tuple[str, InlineKeyboardMarkup]:
    data = draft.data
    names = {m.id: m.display_name for m in await db.scalars(select(Member))}
    lines = ["💸 <b>Record a payment</b>"]
    rows: list[list[InlineKeyboardButton]] = []
    if "from_id" not in data:
        lines.append("Who paid?")
        choices = [(m.id, m.display_name) for m in await active_members(db)]
        action = FROM
    else:
        lines.append(f"From: {html.escape(names[data['from_id']])}")
        choices = [
            (m.id, m.display_name) for m in await active_members(db) if m.id != data["from_id"]
        ]
        action = TO
        if "to_id" in data:
            lines.append(f"To: {html.escape(names[data['to_id']])}")
            choices = []
        else:
            lines.append("Who received it?")
    if "amount_cents" in data:
        lines.append(f"Amount: {fmt_sgd(data['amount_cents'])}")
        rows.append([_button("✅ Record", draft, RECORD)])
    buttons = [_button(name, draft, action, str(member_id)) for member_id, name in choices]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)] + rows
    rows.append([_button("✖ Cancel", draft, CANCEL)])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _show(context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft) -> None:
    text, markup = await render(db, draft)
    await context.bot.edit_message_text(
        text, chat_id=draft.chat_id, message_id=draft.message_id, reply_markup=markup
    )


@admin_only
async def start_settle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    async with db_session(context) as db:
        draft = await create_draft(
            db, kind=KIND, chat_id=message.chat_id, user_id=update.effective_user.id, now=utc_now()
        )
        text, markup = await render(db, draft)
        card = await message.reply_text(text, reply_markup=markup)
        draft.message_id = card.message_id
        await db.commit()


@admin_only
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    callback = decode(query.data)
    async with db_session(context) as db:
        draft = await get_draft(db, callback.draft_id, now=utc_now())
        if draft is None:
            await query.answer("This form has expired. Use /settle again.", show_alert=True)
            return
        if callback.action == TO and int(callback.value) == draft.data.get("from_id"):
            await query.answer("Pick two different people.", show_alert=True)
            return
        await query.answer()
        if callback.action == CANCEL:
            await delete_draft(db, draft)
            await db.commit()
            await query.edit_message_text("✖ Payment not recorded.")
            return
        if callback.action == RECORD:
            await _record(update, context, db, draft)
            return
        if callback.action == FROM:
            update_data(draft, from_id=int(callback.value))
        elif callback.action == TO:
            update_data(draft, to_id=int(callback.value))
            await prompts.ask(
                context.bot,
                draft,
                "how much was paid? (e.g. 23.40)",
                owner=update.effective_user,
                field="amount",
            )
        await _show(context, db, draft)
        await db.commit()


@prompts.on_reply(KIND)
async def on_amount(
    _update: Update, context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft, text: str
) -> None:
    try:
        amount = parse_amount(text, max_cents=MAX_TRANSFER_CENTS)
    except ValueError as error:
        raise prompts.PromptError(str(error)) from None
    update_data(draft, amount_cents=amount)
    await _show(context, db, draft)


async def _record(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft
) -> None:
    data = draft.data
    admin = await upsert_from_telegram(db, update.effective_user)
    matching = next(
        (
            view.line
            for view in await open_lines_for(db, data["from_id"])
            if view.payer.id == data["from_id"]
            and view.payee.id == data["to_id"]
            and view.line.amount_cents == data["amount_cents"]
        ),
        None,
    )
    transfer = Transfer(
        from_member_id=data["from_id"],
        to_member_id=data["to_id"],
        amount_cents=data["amount_cents"],
        invoice_line_id=matching.id if matching else None,
        recorded_by_member_id=admin.id,
    )
    db.add(transfer)
    await db.flush()
    audit.record(
        db,
        actor_member_id=admin.id,
        action="transfer.add",
        after={
            "transfer_id": transfer.id,
            "from_id": transfer.from_member_id,
            "to_id": transfer.to_member_id,
            "amount_cents": transfer.amount_cents,
            "invoice_line_id": transfer.invoice_line_id,
        },
    )
    await delete_draft(db, draft)
    await db.commit()

    names = {m.id: m.display_name for m in await db.scalars(select(Member))}
    payment = (
        f"{html.escape(names[transfer.from_member_id])} paid "
        f"{html.escape(names[transfer.to_member_id])} {fmt_sgd(transfer.amount_cents)}"
    )
    await update.callback_query.edit_message_text(f"✅ Recorded: {payment}.")
    note = " It settles their line on the invoice." if matching else ""
    await announce(
        context.bot, settings_of(context), f"💸 Admin recorded a payment: {payment}.{note}"
    )
    if matching is not None:
        await refresh_invoice_card(context.bot, db, await db.get(Invoice, matching.invoice_id))


def register(application: Application) -> None:
    application.add_handler(CommandHandler("settle", start_settle))
    application.add_handler(CallbackQueryHandler(on_button, pattern=rf"^{CALLBACK_KIND}:\d+:"))
