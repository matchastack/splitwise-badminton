"""/shuttle <price>: a member records a shuttlecock purchase (FR-SH-1, FR-SH-5, FR-SH-6).

The purchase waits until the admin finishes the batch in use with /shuttle_finished,
so recording it closes nothing. Only one purchase can wait at a time. Members record
in the group, like a booking, and are the payer; the admin can record anywhere and
picks the payer, to record a purchase for someone else. Until it is in use, its payer
or the admin can cancel it from its card.
"""

from __future__ import annotations

import html

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from badminton.bot.context import db_session, settings_of
from badminton.bot.keyboards import decode, encode
from badminton.db.models import Draft, Member, ShuttleBatch
from badminton.domain.money import fmt_sgd, parse_amount
from badminton.domain.timeutil import utc_now
from badminton.services import audit
from badminton.services.drafts import create_draft, delete_draft, get_draft, update_data
from badminton.services.members import active_members, upsert_from_telegram
from badminton.services.notify import announce
from badminton.services.shuttle import (
    AlreadyWaiting,
    Removal,
    add_waiting,
    current_batch,
    describe,
    member_names,
    remove_waiting,
    waiting_batch,
)

KIND = "shuttle"
CALLBACK_KIND = "sh"
MAX_SHUTTLE_CENTS = 100_000
USAGE = "Usage: /shuttle &lt;price&gt;, e.g. /shuttle 76.79"
IN_THE_GROUP = "🏸 Send /shuttle &lt;price&gt; in the group, so everyone sees the purchase."
ALREADY_CANCELLED = "This purchase is already cancelled."

PAYER = "pay"
RECORD = "ok"
CANCEL = "x"

ASK = "shc"
YES = "shcy"
KEEP = "shck"

IN_USE = (
    "Batch #{batch_id} is already in use, so it can't be cancelled. "
    "The admin can correct it with /shuttle_edit."
)


def _button(text: str, draft: Draft, action: str, value: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=encode(CALLBACK_KIND, draft.id, action, value))


async def already_waiting(db: AsyncSession, waiting: ShuttleBatch) -> str:
    """The warning when a purchase is refused because another one waits (FR-SH-5)."""
    return (
        f"⚠️ Batch {describe(waiting, await member_names(db))} is already waiting to be "
        "used. Only one purchase can wait at a time, so record this one after the admin "
        "finishes the current batch with /shuttle_finished."
    )


async def use_note(db: AsyncSession) -> str:
    """When a waiting purchase goes into use."""
    current = await current_batch(db)
    if current is None:
        return "It goes into use when the admin starts it with /shuttle_finished."
    return f"It goes into use when the admin finishes batch #{current.id}."


async def render(db: AsyncSession, draft: Draft) -> tuple[str, InlineKeyboardMarkup]:
    data = draft.data
    lines = [f"🏸 <b>New shuttle purchase</b>: {fmt_sgd(data['amount_cents'])}"]
    rows: list[list[InlineKeyboardButton]] = []
    if "payer_id" not in data:
        lines.append("Who paid?")
        members = await active_members(db)
        buttons = [_button(m.display_name, draft, PAYER, str(m.id)) for m in members]
        rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    else:
        payer = await db.get(Member, data["payer_id"])
        lines.append(f"Paid by {html.escape(payer.display_name)}")
        lines.append(await use_note(db))
        rows.append([_button("✅ Record", draft, RECORD)])
    rows.append([_button("✖ Cancel", draft, CANCEL)])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def cancel_markup(batch_id: int) -> InlineKeyboardMarkup:
    """The button on a recorded purchase that lets its payer or the admin cancel it."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🗑 Cancel purchase", callback_data=f"{ASK}:{batch_id}")]]
    )


async def start_shuttle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    is_admin = settings_of(context).is_admin(update.effective_user.id)
    if not is_admin and message.chat.type == ChatType.PRIVATE:
        # Only people in the group spend the group's money, as with /court.
        await message.reply_text(IN_THE_GROUP)
        return
    try:
        amount = parse_amount(" ".join(context.args or ()), max_cents=MAX_SHUTTLE_CENTS)
    except ValueError:
        await message.reply_text(USAGE)
        return
    async with db_session(context) as db:
        sender = await upsert_from_telegram(db, update.effective_user)
        await db.commit()
        if (waiting := await waiting_batch(db)) is not None:
            await message.reply_text(await already_waiting(db, waiting))
            return
        data = {"amount_cents": amount}
        if not is_admin:
            data["payer_id"] = sender.id
        draft = await create_draft(
            db,
            kind=KIND,
            chat_id=message.chat_id,
            user_id=update.effective_user.id,
            now=utc_now(),
            data=data,
        )
        text, markup = await render(db, draft)
        card = await message.reply_text(text, reply_markup=markup)
        draft.message_id = card.message_id
        await db.commit()


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    callback = decode(query.data)
    async with db_session(context) as db:
        draft = await get_draft(db, callback.draft_id, now=utc_now(), lock=True)
        if draft is None:
            await query.answer("This form has expired. Use /shuttle again.", show_alert=True)
            return
        if draft.user_id != update.effective_user.id:
            owner = await db.scalar(select(Member).where(Member.telegram_user_id == draft.user_id))
            await query.answer(f"Only {owner.display_name} can fill this in.", show_alert=True)
            return
        await query.answer()
        if callback.action == CANCEL:
            await delete_draft(context.bot, db, draft)
            await db.commit()
            await query.edit_message_text("✖ Shuttle purchase not recorded.")
            return
        if callback.action == RECORD:
            await _record(update, context, db, draft)
            return
        if callback.action == PAYER:
            update_data(draft, payer_id=int(callback.value))
        text, markup = await render(db, draft)
        await db.commit()
        await query.edit_message_text(text, reply_markup=markup)


async def _record(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft
) -> None:
    data = draft.data
    query = update.callback_query
    try:
        batch = await add_waiting(db, payer_id=data["payer_id"], amount_cents=data["amount_cents"])
    except AlreadyWaiting as refusal:  # recorded since this card opened, or just now
        await delete_draft(context.bot, db, draft)
        await db.commit()
        await query.edit_message_text(await already_waiting(db, refusal.waiting))
        return

    sender = await upsert_from_telegram(db, update.effective_user)
    audit.record(
        db,
        actor_member_id=sender.id,
        action="shuttle.add",
        after={
            "batch_id": batch.id,
            "payer_id": batch.payer_member_id,
            "amount_cents": batch.amount_cents,
        },
    )
    await delete_draft(context.bot, db, draft)
    await db.commit()

    names = await member_names(db)
    payer = html.escape(names[batch.payer_member_id])
    await query.edit_message_text(
        f"✅ Recorded shuttle purchase #{batch.id}.", reply_markup=cancel_markup(batch.id)
    )
    await announce(
        context.bot,
        settings_of(context),
        f"🛍 <b>New shuttle batch #{batch.id}</b>: {fmt_sgd(batch.amount_cents)}, "
        f"paid by {payer}. {await use_note(db)}",
    )


async def on_cancel_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action, batch_id = query.data.split(":")
    async with db_session(context) as db:
        batch = await db.get(ShuttleBatch, int(batch_id))
        if batch is None:
            await query.answer(ALREADY_CANCELLED, show_alert=True)
            await query.edit_message_reply_markup(None)
            return
        payer = await db.get(Member, batch.payer_member_id)
        user_id = update.effective_user.id
        if user_id != payer.telegram_user_id and not settings_of(context).is_admin(user_id):
            await query.answer(
                f"Only {payer.display_name} or the admin can cancel this purchase.",
                show_alert=True,
            )
            return
        if not batch.waiting:
            await query.answer(IN_USE.format(batch_id=batch.id), show_alert=True)
            await query.edit_message_reply_markup(None)
            return

        if action == ASK:
            await query.answer()
            await query.edit_message_reply_markup(
                InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "Yes, cancel it", callback_data=f"{YES}:{batch.id}"
                            ),
                            InlineKeyboardButton("Keep it", callback_data=f"{KEEP}:{batch.id}"),
                        ]
                    ]
                )
            )
            return
        if action == KEEP:
            await query.answer()
            await query.edit_message_reply_markup(cancel_markup(batch.id))
            return

        names = await member_names(db)
        if (removal := await remove_waiting(db, batch)) is not Removal.REMOVED:
            # Opened, or cancelled by a second tap, since the checks above.
            alert = ALREADY_CANCELLED if removal is Removal.GONE else IN_USE
            await query.answer(alert.format(batch_id=batch.id), show_alert=True)
            await query.edit_message_reply_markup(None)
            return
        actor = await upsert_from_telegram(db, update.effective_user)
        audit.record(
            db,
            actor_member_id=actor.id,
            action="shuttle.cancel",
            before={
                "batch_id": batch.id,
                "payer_id": batch.payer_member_id,
                "amount_cents": batch.amount_cents,
            },
        )
        await db.commit()

        who = html.escape(actor.display_name)
        await query.answer("Purchase cancelled.")
        await query.edit_message_text(
            f"🗑 <s>Shuttle purchase {describe(batch, names)}</s>\nCancelled by {who}."
        )
        await announce(
            context.bot,
            settings_of(context),
            f"🗑 {who} cancelled shuttle purchase {describe(batch, names)}. "
            "It wasn't in use yet, so nobody is charged for it.",
        )


def register(application: Application) -> None:
    application.add_handler(CommandHandler("shuttle", start_shuttle))
    application.add_handler(CallbackQueryHandler(on_button, pattern=rf"^{CALLBACK_KIND}:\d+:"))
    application.add_handler(
        CallbackQueryHandler(on_cancel_button, pattern=rf"^({ASK}|{YES}|{KEEP}):\d+$")
    )
