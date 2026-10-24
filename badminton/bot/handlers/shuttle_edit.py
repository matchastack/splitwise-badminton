"""/shuttle_edit: the admin corrects a shuttle batch (FR-SH-7, FR-ADM-3, FR-ADM-5).

Flow: batch picker → batch menu → price (reply prompt) or payer, removing a purchase
that is still waiting, or undoing /shuttle_finished on the batch in use. Every change
is announced in the group, with the recalculated split for a finished batch, and audited.
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
from badminton.bot.handlers.shuttle import MAX_SHUTTLE_CENTS
from badminton.bot.keyboards import decode, encode
from badminton.db.models import Draft, ShuttleBatch
from badminton.domain.money import fmt_sgd, parse_amount
from badminton.domain.timeutil import fmt_date, sgt_date, utc_now
from badminton.services import audit
from badminton.services.drafts import create_draft, delete_draft, get_draft, update_data
from badminton.services.members import active_members, upsert_from_telegram
from badminton.services.notify import announce
from badminton.services.shuttle import (
    Removal,
    ReopenRefused,
    check_can_reopen,
    current_batch,
    describe,
    member_names,
    next_start,
    previous_batch,
    remove_waiting,
    reopen,
    split_lines,
    waiting_batch,
)

KIND = "shuttle_edit"
CALLBACK_KIND = "se"
RECENT_BATCHES = 7  # the one in use and the last few finished

BATCHES = "list"
PICK_BATCH = "b"
PRICE = "pr"
PAYER = "py"
SET_PAYER = "sp"
REMOVE = "rm"
CONFIRM_REMOVE = "rmy"
REOPEN = "ro"
CONFIRM_REOPEN = "roy"
CLOSE = "x"

WAITING = "waiting"
IN_USE = "in use"
FINISHED = "finished"


def _button(text: str, draft: Draft, action: str, value: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=encode(CALLBACK_KIND, draft.id, action, value))


def _status(batch: ShuttleBatch, current: ShuttleBatch | None) -> str:
    if batch.waiting:
        return WAITING
    return IN_USE if current is not None and current.id == batch.id else FINISHED


async def render(db: AsyncSession, draft: Draft) -> tuple[str, InlineKeyboardMarkup]:
    data = draft.data
    names = await member_names(db)
    screen = data.get("screen", BATCHES)
    if screen == BATCHES:
        opened = await db.scalars(
            select(ShuttleBatch)
            .where(ShuttleBatch.opened_at.is_not(None))
            .order_by(ShuttleBatch.starts_at.desc())
            .limit(RECENT_BATCHES)
        )
        waiting = await waiting_batch(db)
        current = await current_batch(db)
        batches = [*([waiting] if waiting is not None else []), *opened]  # newest first
        rows = []
        for batch in batches:
            label = (
                f"#{batch.id} · {_status(batch, current)} · {fmt_sgd(batch.amount_cents)} · "
                f"{names[batch.payer_member_id]}"
            )
            rows.append([_button(label, draft, PICK_BATCH, str(batch.id))])
        text = (
            "✏️ <b>Edit a shuttle batch</b>\nWhich one?"
            if rows
            else "There are no shuttle batches to edit."
        )
        return text, InlineKeyboardMarkup([*rows, [_button("✖ Close", draft, CLOSE)]])

    batch = await db.get(ShuttleBatch, data["batch_id"])
    status = _status(batch, await current_batch(db))
    title = f"✏️ <b>Shuttle batch {describe(batch, names)}</b>\n{await _when(db, batch, status)}"
    back = _button("‹ Back", draft, PICK_BATCH, str(batch.id))
    if screen == PAYER:
        buttons = [
            _button(m.display_name, draft, SET_PAYER, str(m.id)) for m in await active_members(db)
        ]
        rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)] + [[back]]
        return f"{title}\nWho paid for it?", InlineKeyboardMarkup(rows)
    if screen == REMOVE:
        rows = [[_button("Yes, remove it", draft, CONFIRM_REMOVE)], [back]]
        text = f"{title}\nRemove this purchase? It isn't in use, so nobody is charged for it."
        return text, InlineKeyboardMarkup(rows)
    if screen == REOPEN:
        rows = [[_button("Yes, put it back", draft, CONFIRM_REOPEN)], [back]]
        previous = data.get("previous_id")
        then = (
            f"Batch #{previous} will be in use again, and its split is withdrawn."
            if previous
            else "No batch will be in use until /shuttle_finished."
        )
        return f"{title}\nPut this batch back to waiting? {then}", InlineKeyboardMarkup(rows)

    rows = [[_button("💵 Price", draft, PRICE), _button("👤 Payer", draft, PAYER)]]
    if status == WAITING:
        rows.append([_button("🗑 Remove", draft, REMOVE)])
    elif status == IN_USE:
        rows.append([_button("↩️ Undo /shuttle_finished", draft, REOPEN)])
    rows.append([_button("‹ Batches", draft, BATCHES), _button("✖ Close", draft, CLOSE)])
    return f"{title}\nWhat do you want to change?", InlineKeyboardMarkup(rows)


async def _when(db: AsyncSession, batch: ShuttleBatch, status: str) -> str:
    if status == WAITING:
        return "Waiting to be used."
    since = fmt_date(sgt_date(batch.starts_at))
    if status == IN_USE:
        return f"In use since {since}."
    until = fmt_date(sgt_date(await next_start(db, batch)))
    return f"Used from {since} until {until}."


@admin_only
async def start_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    async with db_session(context) as db:
        draft = await create_draft(
            db,
            kind=KIND,
            chat_id=message.chat_id,
            user_id=update.effective_user.id,
            now=utc_now(),
            data={"screen": BATCHES},
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
        draft = await get_draft(db, callback.draft_id, now=utc_now(), lock=True)
        if draft is None:
            await query.answer("This form has expired. Use /shuttle_edit again.", show_alert=True)
            return
        action, value = callback.action, callback.value
        if action != PRICE:
            await prompts.dismiss(context.bot, draft)
        if action == CLOSE:
            await query.answer()
            await delete_draft(context.bot, db, draft)
            await db.commit()
            await query.edit_message_text("✏️ Editing closed.")
            return
        batch_id = int(value) if action == PICK_BATCH else draft.data.get("batch_id")
        if action != BATCHES and await db.get(ShuttleBatch, batch_id) is None:
            await query.answer(f"Batch #{batch_id} no longer exists.", show_alert=True)
            await _back_to_batches(context, db, draft)
            await db.commit()
            return
        alert = await _apply(update, context, db, draft, action, value)
        await query.answer(alert, show_alert=alert is not None)
        await db.commit()


async def _apply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db: AsyncSession,
    draft: Draft,
    action: str,
    value: str,
) -> str | None:
    """Apply one button press. Returns an alert to show, if any."""
    if action == SET_PAYER:
        await _change(update, context, db, draft, payer_id=int(value))
        return None
    if action == CONFIRM_REMOVE:
        return await _remove(update, context, db, draft)
    if action == CONFIRM_REOPEN:
        return await _reopen(update, context, db, draft)
    if action == PRICE:
        batch_id = draft.data["batch_id"]
        await prompts.ask(
            context.bot,
            draft,
            f"what's the correct price for shuttle batch #{batch_id}? (e.g. 76.79)",
            owner=update.effective_user,
            field="price",
        )
        return None
    if action == BATCHES:
        update_data(draft, screen=BATCHES, batch_id=None, previous_id=None)
    elif action == PICK_BATCH:
        update_data(draft, screen="menu", batch_id=int(value), previous_id=None)
    elif action in (PAYER, REMOVE):
        update_data(draft, screen=action)
    elif action == REOPEN:
        batch = await db.get(ShuttleBatch, draft.data["batch_id"])
        try:
            await check_can_reopen(db, batch)
        except ReopenRefused as refusal:
            return str(refusal)
        previous = await previous_batch(db, batch)
        update_data(draft, screen=REOPEN, previous_id=previous.id if previous else None)
    await _show(context, db, draft)
    return None


async def _show(context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft) -> None:
    text, markup = await render(db, draft)
    await context.bot.edit_message_text(
        text, chat_id=draft.chat_id, message_id=draft.message_id, reply_markup=markup
    )


async def _back_to_batches(
    context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft
) -> None:
    """The chosen batch is gone (its buyer cancelled it): show the list again. An open
    price prompt goes too, or an answer to it would find no batch."""
    await prompts.dismiss(context.bot, draft)
    update_data(draft, screen=BATCHES, batch_id=None, previous_id=None)
    await _show(context, db, draft)


def _details(batch: ShuttleBatch) -> dict[str, int]:
    return {"amount_cents": batch.amount_cents, "payer_id": batch.payer_member_id}


async def _change(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db: AsyncSession,
    draft: Draft,
    *,
    amount_cents: int | None = None,
    payer_id: int | None = None,
) -> None:
    """Change a batch's price or payer, then announce and audit."""
    batch = await db.get(ShuttleBatch, draft.data["batch_id"])
    names = await member_names(db)
    before = _details(batch)
    if amount_cents is not None:
        change = f"price {fmt_sgd(batch.amount_cents)} → {fmt_sgd(amount_cents)}"
        batch.amount_cents = amount_cents
    else:
        change = (
            f"paid by {html.escape(names[batch.payer_member_id])} → {html.escape(names[payer_id])}"
        )
        batch.payer_member_id = payer_id
    on_menu = draft.data.get("screen") == "menu"
    update_data(draft, screen="menu")
    if _details(batch) == before:
        if not on_menu:  # back from the payer picker; the menu itself is unchanged
            await _show(context, db, draft)
        await db.commit()
        return
    admin = await upsert_from_telegram(db, update.effective_user)
    audit.record(
        db,
        actor_member_id=admin.id,
        action="shuttle.edit",
        batch_id=batch.id,
        before=before,
        after=_details(batch),
    )
    await db.commit()

    lines = [f"✏️ Admin changed shuttle batch #{batch.id}: {change}"]
    if _status(batch, await current_batch(db)) == FINISHED:
        lines += await split_lines(
            db,
            batch,
            names,
            utc_now(),
            heading=f"Recalculated batch #{batch.id}",
            footer="Any difference shows as an adjustment on the next monthly statement.",
        )
    await _show(context, db, draft)
    await announce(context.bot, settings_of(context), "\n".join(lines))


async def _remove(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft
) -> str | None:
    batch = await db.get(ShuttleBatch, draft.data["batch_id"])
    names = await member_names(db)
    removal = await remove_waiting(db, batch)
    if removal is Removal.GONE:  # its buyer cancelled it at the same moment
        await _back_to_batches(context, db, draft)
        return f"Batch #{batch.id} no longer exists."
    if removal is Removal.IN_USE:
        update_data(draft, screen="menu")
        await _show(context, db, draft)
        return f"Batch #{batch.id} is in use now, so it can't be removed."
    admin = await upsert_from_telegram(db, update.effective_user)
    audit.record(
        db,
        actor_member_id=admin.id,
        action="shuttle.delete",
        batch_id=batch.id,
        before=_details(batch),
    )
    await delete_draft(context.bot, db, draft)
    await db.commit()
    await update.callback_query.edit_message_text(f"🗑 Removed shuttle batch #{batch.id}.")
    await announce(
        context.bot,
        settings_of(context),
        f"🗑 Admin removed shuttle batch {describe(batch, names)}. "
        "It wasn't in use yet, so nobody is charged for it.",
    )
    return None


async def _reopen(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft
) -> str | None:
    batch = await db.get(ShuttleBatch, draft.data["batch_id"])
    started = batch.starts_at
    try:
        previous = await reopen(db, batch)
    except ReopenRefused as refusal:
        update_data(draft, screen="menu", previous_id=None)
        await _show(context, db, draft)
        return str(refusal)
    admin = await upsert_from_telegram(db, update.effective_user)
    audit.record(
        db,
        actor_member_id=admin.id,
        action="shuttle.reopen",
        batch_id=batch.id,
        before={"in_use": batch.id, "starts_at": started},
        after={"in_use": previous.id if previous is not None else None},
    )
    await delete_draft(context.bot, db, draft)
    await db.commit()

    names = await member_names(db)
    await update.callback_query.edit_message_text(f"↩️ Batch #{batch.id} is waiting again.")
    if previous is not None:
        text = (
            f"↩️ Admin undid finishing batch #{previous.id}: it's in use again, and its split "
            f"is withdrawn. Batch {describe(batch, names)} is waiting again."
        )
    else:
        text = (
            f"↩️ Admin put batch {describe(batch, names)} back to waiting. "
            "No batch is in use until the admin runs /shuttle_finished."
        )
    await announce(context.bot, settings_of(context), text)
    return None


@prompts.on_reply(KIND)
async def on_typed_answer(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft, text: str
) -> None:
    try:
        amount_cents = parse_amount(text, max_cents=MAX_SHUTTLE_CENTS)
    except ValueError as error:
        raise prompts.PromptError(str(error)) from None
    if await db.get(ShuttleBatch, draft.data["batch_id"]) is None:  # cancelled meanwhile
        update_data(draft, screen=BATCHES, batch_id=None)
        await _show(context, db, draft)
        return
    await _change(update, context, db, draft, amount_cents=amount_cents)


def register(application: Application) -> None:
    application.add_handler(CommandHandler("shuttle_edit", start_edit))
    application.add_handler(CallbackQueryHandler(on_button, pattern=rf"^{CALLBACK_KIND}:\d+:"))
