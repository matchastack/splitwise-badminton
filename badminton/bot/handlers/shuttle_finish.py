"""/shuttle_finished: the admin finishes the batch in use (FR-SH-2, FR-SH-3).

The waiting purchase takes over from the next session, or from the last one when
its shuttles were already used there. The announcement shows how the finished batch
was split by hours played.
"""

from __future__ import annotations

import html

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from badminton.bot.admin import admin_only
from badminton.bot.context import db_session, settings_of
from badminton.bot.keyboards import decode, encode
from badminton.db.models import Draft, Session, ShuttleBatch
from badminton.domain.timeutil import fmt_date, sgt_date, utc_now
from badminton.services import audit
from badminton.services.drafts import create_draft, delete_draft, get_draft, update_data
from badminton.services.members import upsert_from_telegram
from badminton.services.notify import announce
from badminton.services.shuttle import (
    current_batch,
    describe,
    member_names,
    open_waiting,
    split_lines,
    start_options,
    waiting_batch,
)

KIND = "shuttle_finish"
CALLBACK_KIND = "sf"

START_NEXT = "next"
START_LAST = "last"
CHANGE = "back"
FINISH = "ok"
CANCEL = "x"

NOTHING_WAITING = (
    "There's no new shuttle batch waiting to take over. "
    "Record the next purchase with /shuttle &lt;price&gt; first."
)


def _button(text: str, draft: Draft, action: str, value: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=encode(CALLBACK_KIND, draft.id, action, value))


async def render(
    db: AsyncSession, draft: Draft, batch: ShuttleBatch
) -> tuple[str, InlineKeyboardMarkup]:
    data = draft.data
    names = await member_names(db)
    current = await current_batch(db)
    if current is not None:
        lines = [
            f"🏁 <b>Finish shuttle batch #{current.id}?</b>",
            f"Batch {describe(batch, names)} takes over.",
        ]
        confirm = f"✅ Finish batch #{current.id}"
    else:
        lines = [f"🏸 <b>Start shuttle batch {describe(batch, names)}?</b>"]
        confirm = f"✅ Start batch #{batch.id}"
    rows: list[list[InlineKeyboardButton]] = []
    if "start" not in data:
        lines.append(f"Which session is the first to use batch #{batch.id}?")
        options = await start_options(db, utc_now())
        if options.next_session is not None:
            label = f"Next session ({fmt_date(sgt_date(options.next_session.starts_at))})"
        else:
            label = "From the next session on"
        rows.append([_button(label, draft, START_NEXT)])
        if options.last_session is not None:
            label = f"Last session ({fmt_date(sgt_date(options.last_session.starts_at))})"
            rows.append([_button(label, draft, START_LAST, str(options.last_session.id))])
    else:
        lines.append(f"Used from: {html.escape(data['start_label'])}")
        rows.append([_button(confirm, draft, FINISH), _button("‹ Change", draft, CHANGE)])
    rows.append([_button("✖ Cancel", draft, CANCEL)])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


@admin_only
async def start_finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    async with db_session(context) as db:
        batch = await waiting_batch(db)
        if batch is None:
            await message.reply_text(NOTHING_WAITING)
            return
        draft = await create_draft(
            db,
            kind=KIND,
            chat_id=message.chat_id,
            user_id=update.effective_user.id,
            now=utc_now(),
            data={"batch_id": batch.id},
        )
        text, markup = await render(db, draft, batch)
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
            await query.answer(
                "This form has expired. Use /shuttle_finished again.", show_alert=True
            )
            return
        if callback.action == CANCEL:
            await query.answer()
            await delete_draft(context.bot, db, draft)
            await db.commit()
            await query.edit_message_text("✖ No batch was finished.")
            return
        batch = await db.get(ShuttleBatch, draft.data["batch_id"])
        if batch is None or not batch.waiting:
            await _no_longer_waiting(update, context, db, draft)
            return
        if callback.action == FINISH:
            await _finish(update, context, db, draft)
            return
        await query.answer()
        if callback.action == START_NEXT:
            update_data(draft, start="next", start_label="the next session")
        elif callback.action == START_LAST:
            session = await db.get(Session, int(callback.value))
            label = f"{fmt_date(sgt_date(session.starts_at))} (the last session)"
            update_data(draft, start="last", start_session_id=session.id, start_label=label)
        elif callback.action == CHANGE:
            update_data(draft, start=None, start_session_id=None, start_label=None)
        text, markup = await render(db, draft, batch)
        await db.commit()
        await query.edit_message_text(text, reply_markup=markup)


async def _no_longer_waiting(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft
) -> None:
    batch_id = draft.data["batch_id"]
    await update.callback_query.answer(
        f"Batch #{batch_id} is no longer waiting, so nothing changed.", show_alert=True
    )
    await delete_draft(context.bot, db, draft)
    await db.commit()
    await update.callback_query.edit_message_text(
        f"✖ Batch #{batch_id} is no longer waiting, so nothing changed."
    )


async def _finish(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft
) -> None:
    data = draft.data
    now = utc_now()
    starts_at = now
    if data["start"] == "last":
        starts_at = (await db.get(Session, data["start_session_id"])).starts_at
    previous = await current_batch(db)
    if previous is not None and starts_at <= previous.starts_at:
        await update.callback_query.answer(
            f"That session already counts towards batch #{previous.id}. Pick the next one.",
            show_alert=True,
        )
        return
    batch = await open_waiting(db, data["batch_id"], starts_at, now)
    if batch is None:
        await _no_longer_waiting(update, context, db, draft)
        return
    await update.callback_query.answer()
    admin = await upsert_from_telegram(db, update.effective_user)
    audit.record(
        db,
        actor_member_id=admin.id,
        action="shuttle.finish",
        batch_id=batch.id,
        before={"in_use": previous.id if previous is not None else None},
        after={"in_use": batch.id, "starts_at": starts_at},
    )
    await delete_draft(context.bot, db, draft)
    await db.commit()

    names = await member_names(db)
    lines = []
    if previous is not None:
        heading = f"🏁 <b>Batch #{previous.id} is finished</b>"
        lines += [*await split_lines(db, previous, names, now, heading=heading), ""]
        done = f"✅ Batch #{previous.id} is finished; batch #{batch.id} is in use."
    else:
        done = f"✅ Batch #{batch.id} is in use."
    lines.append(
        f"🏸 Batch {describe(batch, names)} is in use from {html.escape(data['start_label'])}."
    )
    await update.callback_query.edit_message_text(done)
    await announce(context.bot, settings_of(context), "\n".join(lines))


def register(application: Application) -> None:
    application.add_handler(CommandHandler("shuttle_finished", start_finish))
    application.add_handler(CallbackQueryHandler(on_button, pattern=rf"^{CALLBACK_KIND}:\d+:"))
