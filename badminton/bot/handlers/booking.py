"""The booking wizard (FR-BK-1 to FR-BK-9).

A member sends their booking screenshot with the caption /book. The bot replies
with one card that is edited in place as they pick the date, start time and
duration. Only the submitter can operate it (FR-BK-2); the sender is the payer.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from badminton.bot import prompts
from badminton.bot.context import db_session
from badminton.bot.handlers.guard import BOOK_PHOTO
from badminton.bot.keyboards import (
    BACK,
    NOOP,
    PICK_DATE,
    PICK_HOUR,
    SHOW_MONTH,
    calendar_keyboard,
    decode,
    encode,
    hour_keyboard,
    parse_day,
    parse_month,
)
from badminton.db.models import Draft, Member
from badminton.domain.timeutil import (
    at_sgt,
    fmt_date,
    fmt_hours,
    fmt_session,
    parse_hours,
    sgt_date,
    utc_now,
)
from badminton.services.drafts import create_draft, delete_draft, get_draft, update_data
from badminton.services.members import mention_html, upsert_from_telegram

KIND = "booking"
CALLBACK_KIND = "bk"

PICK_DURATION = "dur"
OTHER_DURATION = "durx"
BACK_TO_HOUR = "bkh"
CANCEL = "x"

STEP_DATE = "date"
STEP_HOUR = "hour"
STEP_DURATION = "duration"
STEP_PRICE = "price"

EXPIRED = "This booking form has expired. Send your screenshot with /book again."


# Rendering --------------------------------------------------------------------


def render(draft: Draft, payer: Member) -> tuple[str, InlineKeyboardMarkup]:
    """The wizard card for the draft's current step."""
    data = draft.data
    lines = [f"🏸 <b>New court booking</b> by {mention_html(payer)}"]
    if "day" in data:
        day = parse_day(data["day"])
        if "hour" in data and "minutes" in data:
            start = at_sgt(day, data["hour"])
            end = start + timedelta(minutes=data["minutes"])
            lines.append(f"📅 {fmt_session(start, end)} ({fmt_hours(data['minutes'])})")
        elif "hour" in data:
            lines.append(f"📅 {fmt_date(day)}, {data['hour']:02d}:00")
        else:
            lines.append(f"📅 {fmt_date(day)}")

    step = data["step"]
    if step == STEP_DATE:
        lines.append("Which day is the court booked for?")
        today = sgt_date(utc_now())
        month = parse_month(data["month"]) if "month" in data else today
        markup = calendar_keyboard(CALLBACK_KIND, draft.id, month, today)
    elif step == STEP_HOUR:
        lines.append("What time does it start?")
        markup = hour_keyboard(CALLBACK_KIND, draft.id)
    elif step == STEP_DURATION:
        lines.append("How long is it booked for?")
        markup = InlineKeyboardMarkup(
            [
                [
                    _button("1h", draft, PICK_DURATION, "60"),
                    _button("2h", draft, PICK_DURATION, "120"),
                    _button("Other…", draft, OTHER_DURATION),
                ],
                [_button("‹ Change time", draft, BACK_TO_HOUR)],
            ]
        )
    else:
        lines.append("How much did you pay?")
        markup = InlineKeyboardMarkup([])
    rows = [*markup.inline_keyboard, [_button("✖ Cancel", draft, CANCEL)]]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _button(text: str, draft: Draft, action: str, value: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=encode(CALLBACK_KIND, draft.id, action, value))


async def refresh_card(context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft) -> None:
    payer = await db.get(Member, draft.data["payer_member_id"])
    text, markup = render(draft, payer)
    await context.bot.edit_message_text(
        text, chat_id=draft.chat_id, message_id=draft.message_id, reply_markup=markup
    )


# Handlers ---------------------------------------------------------------------


async def start_booking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    async with db_session(context) as db:
        payer = await upsert_from_telegram(db, update.effective_user)
        draft = await create_draft(
            db,
            kind=KIND,
            chat_id=message.chat_id,
            user_id=update.effective_user.id,
            now=utc_now(),
            data={
                "step": STEP_DATE,
                "payer_member_id": payer.id,
                "photo_file_id": message.photo[-1].file_id,
                "photo_message_id": message.message_id,
            },
        )
        text, markup = render(draft, payer)
        card = await message.reply_text(text, reply_markup=markup)
        draft.message_id = card.message_id
        await db.commit()


async def book_without_photo(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "📸 Send your booking screenshot with the caption /book to record a court booking."
    )


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    callback = decode(query.data)
    async with db_session(context) as db:
        draft = await get_draft(db, callback.draft_id, now=utc_now())
        if draft is None:
            await query.answer(EXPIRED, show_alert=True)
            return
        if draft.user_id != update.effective_user.id:
            payer = await db.get(Member, draft.data["payer_member_id"])
            await query.answer(f"Only {payer.display_name} can fill this in.", show_alert=True)
            return

        alert = await _apply(update, context, db, draft, callback.action, callback.value)
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
    changes: dict[str, Any]
    if action == NOOP:
        return None
    if action == CANCEL:
        await delete_draft(db, draft)
        await update.callback_query.edit_message_text("✖ Booking cancelled.")
        return None
    if action == SHOW_MONTH:
        changes = {"month": value}
    elif action == PICK_DATE:
        if parse_day(value) < sgt_date(utc_now()):
            return "That date has passed."
        changes = {"day": value, "month": value[:6], "step": STEP_HOUR}
    elif action == BACK:
        changes = {"step": STEP_DATE, "hour": None, "minutes": None}
    elif action == PICK_HOUR:
        changes = {"hour": int(value), "step": STEP_DURATION}
    elif action == BACK_TO_HOUR:
        changes = {"step": STEP_HOUR, "minutes": None}
    elif action == PICK_DURATION:
        changes = {"minutes": int(value), "step": STEP_PRICE}
    elif action == OTHER_DURATION:
        await prompts.ask(
            context.bot,
            draft,
            "how many hours is it booked for? (e.g. 3 or 1.5)",
            owner=update.effective_user,
            field="minutes",
        )
        return None
    else:
        return None
    update_data(draft, **changes)
    await refresh_card(context, db, draft)
    return None


@prompts.on_reply(KIND)
async def on_typed_answer(
    _update: Update, context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft, text: str
) -> None:
    if draft.data.get("awaiting") == "minutes":
        try:
            minutes = parse_hours(text)
        except ValueError as error:
            raise prompts.PromptError(str(error)) from None
        update_data(draft, minutes=minutes, step=STEP_PRICE)
    await refresh_card(context, db, draft)


def register(application: Application) -> None:
    application.add_handler(MessageHandler(filters.ChatType.GROUPS & BOOK_PHOTO, start_booking))
    application.add_handler(
        CommandHandler("book", book_without_photo, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(CallbackQueryHandler(on_button, pattern=rf"^{CALLBACK_KIND}:\d+:"))
