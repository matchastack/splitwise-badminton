"""/shuttle <price>: the admin records a shuttlecock purchase (FR-SH-1 to FR-SH-3).

Recording a batch closes the previous one, and the announcement shows how that
batch was split by hours played.
"""

from __future__ import annotations

import html

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from badminton.bot.admin import admin_only
from badminton.bot.context import db_session, settings_of
from badminton.bot.keyboards import decode, encode
from badminton.db.models import Draft, Member, Session, ShuttleBatch
from badminton.domain.money import fmt_sgd, parse_amount
from badminton.domain.timeutil import fmt_date, fmt_hours, sgt_date, utc_now
from badminton.services import audit
from badminton.services.drafts import create_draft, delete_draft, get_draft, update_data
from badminton.services.members import active_members, upsert_from_telegram
from badminton.services.notify import announce
from badminton.services.repo import load_ledger
from badminton.services.shuttle import closed_batch, split_as_of, start_options

KIND = "shuttle"
CALLBACK_KIND = "sh"
MAX_SHUTTLE_CENTS = 100_000
USAGE = "Usage: /shuttle &lt;price&gt;, e.g. /shuttle 76.79"

PAYER = "pay"
START_NEXT = "next"
START_LAST = "last"
RECORD = "ok"
CANCEL = "x"


def _button(text: str, draft: Draft, action: str, value: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=encode(CALLBACK_KIND, draft.id, action, value))


async def render(db: AsyncSession, draft: Draft) -> tuple[str, InlineKeyboardMarkup]:
    data = draft.data
    lines = [f"🏸 <b>New shuttle batch</b>: {fmt_sgd(data['amount_cents'])}"]
    rows: list[list[InlineKeyboardButton]] = []
    if "payer_id" not in data:
        lines.append("Who paid?")
        members = await active_members(db)
        buttons = [_button(m.display_name, draft, PAYER, str(m.id)) for m in members]
        rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    else:
        payer = await db.get(Member, data["payer_id"])
        lines.append(f"Paid by {html.escape(payer.display_name)}")
        if "start" not in data:
            lines.append("Which session is the first to use it?")
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
            rows.append([_button("✅ Record", draft, RECORD)])
    rows.append([_button("✖ Cancel", draft, CANCEL)])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


@admin_only
async def start_shuttle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    try:
        amount = parse_amount(" ".join(context.args or ()), max_cents=MAX_SHUTTLE_CENTS)
    except ValueError:
        await message.reply_text(USAGE)
        return
    async with db_session(context) as db:
        draft = await create_draft(
            db,
            kind=KIND,
            chat_id=message.chat_id,
            user_id=update.effective_user.id,
            now=utc_now(),
            data={"amount_cents": amount},
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
            await query.answer("This form has expired. Use /shuttle again.", show_alert=True)
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
        elif callback.action == START_NEXT:
            update_data(draft, start="next", start_label="the next session")
        elif callback.action == START_LAST:
            session = await db.get(Session, int(callback.value))
            label = f"{fmt_date(sgt_date(session.starts_at))} (the last session)"
            update_data(draft, start="last", start_session_id=session.id, start_label=label)
        text, markup = await render(db, draft)
        await db.commit()
        await query.edit_message_text(text, reply_markup=markup)


async def _record(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft
) -> None:
    data = draft.data
    now = utc_now()
    if data["start"] == "last":
        starts_at = (await db.get(Session, data["start_session_id"])).starts_at
    else:
        starts_at = now
    previous = await db.scalar(
        select(ShuttleBatch)
        .where(ShuttleBatch.starts_at < starts_at)
        .order_by(ShuttleBatch.starts_at.desc())
        .limit(1)
    )
    batch = ShuttleBatch(
        payer_member_id=data["payer_id"], amount_cents=data["amount_cents"], starts_at=starts_at
    )
    db.add(batch)
    admin = await upsert_from_telegram(db, update.effective_user)
    await db.flush()
    audit.record(
        db,
        actor_member_id=admin.id,
        action="shuttle.add",
        after={
            "batch_id": batch.id,
            "payer_id": batch.payer_member_id,
            "amount_cents": batch.amount_cents,
            "starts_at": starts_at,
        },
    )
    await delete_draft(context.bot, db, draft)
    await db.commit()

    names = {m.id: m.display_name for m in await db.scalars(select(Member))}
    lines = [
        f"🏸 <b>New shuttle batch #{batch.id}</b>: {fmt_sgd(batch.amount_cents)}, "
        f"paid by {html.escape(names[batch.payer_member_id])}. "
        f"It's used from {html.escape(data['start_label'])}."
    ]
    if previous is not None:
        lines.append("")
        lines.extend(await _closed_lines(db, previous, batch, names))
    await update.callback_query.edit_message_text(f"✅ Recorded shuttle batch #{batch.id}.")
    await announce(context.bot, settings_of(context), "\n".join(lines))


async def _closed_lines(
    db: AsyncSession, previous: ShuttleBatch, batch: ShuttleBatch, names: dict[int, str]
) -> list[str]:
    # A session in progress belongs to the closed batch, so count it as played.
    as_of = await split_as_of(db, batch.starts_at, utc_now())
    split = closed_batch(await load_ledger(db, as_of), previous.id)
    if split is None or not split.shares:
        payer = html.escape(names[previous.payer_member_id])
        return [
            f"Batch #{previous.id} is finished. Nobody played with it, so it stays with {payer}."
        ]
    per_hour = fmt_sgd(round(split.amount_cents * 60 / split.minutes))
    lines = [
        f"Batch #{previous.id} is finished: {fmt_sgd(split.amount_cents)} over "
        f"{fmt_hours(split.minutes)} = {per_hour}/h"
    ]
    lines += [
        f"• {html.escape(names[member_id])} {fmt_hours(minutes)} — {fmt_sgd(cents)}"
        for member_id, minutes, cents in split.shares
    ]
    lines.append("These amounts go on the next monthly invoice.")
    return lines


def register(application: Application) -> None:
    application.add_handler(CommandHandler("shuttle", start_shuttle))
    application.add_handler(CallbackQueryHandler(on_button, pattern=rf"^{CALLBACK_KIND}:\d+:"))
