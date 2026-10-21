"""The booking wizard (FR-BK-1 to FR-BK-9).

A member sends their booking screenshot with the caption /book. The bot replies
with one card that is edited in place as they pick the date, start time and
duration. Only the submitter can operate it (FR-BK-2); the sender is the payer.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
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
from badminton.bot.context import db_session, settings_of
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
from badminton.db.models import Draft, Member, Session
from badminton.domain.money import fmt_sgd, parse_amount
from badminton.domain.timeutil import (
    at_sgt,
    card_due_date,
    fmt_date,
    fmt_hours,
    fmt_session,
    parse_hours,
    sgt_date,
    utc_now,
)
from badminton.services.attendance import post_card_if_due, refresh_card
from badminton.services.drafts import create_draft, delete_draft, get_draft, update_data
from badminton.services.members import mention_html, upsert_from_telegram
from badminton.services.repo import last_hourly_rate
from badminton.services.sessions import find_mergeable_session, record_booking

KIND = "booking"
CALLBACK_KIND = "bk"

PICK_DURATION = "dur"
OTHER_DURATION = "durx"
BACK_TO_HOUR = "bkh"
PICK_PRICE = "p"
OTHER_PRICE = "px"
BACK_TO_DURATION = "bkd"
CONFIRM = "ok"
MERGE = "mg"
SEPARATE = "sep"
CANCEL = "x"

STEP_DATE = "date"
STEP_HOUR = "hour"
STEP_DURATION = "duration"
STEP_PRICE = "price"
STEP_MERGE = "merge"
STEP_CONFIRM = "confirm"

EXPIRED = "This booking form has expired. Send your screenshot with /book again."


# Rendering --------------------------------------------------------------------


def render(
    draft: Draft, payer: Member, merge_into: Session | None = None
) -> tuple[str, InlineKeyboardMarkup]:
    """The wizard card for the draft's current step. ``merge_into`` is the session the
    booking may join (merge step) or will join (confirm step)."""
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
    elif step == STEP_PRICE:
        lines.append("How much did you pay?")
        suggested = data["suggested_cents"]
        markup = InlineKeyboardMarkup(
            [
                [
                    _button(fmt_sgd(suggested), draft, PICK_PRICE, str(suggested)),
                    _button("Other…", draft, OTHER_PRICE),
                ],
                [_button("‹ Change duration", draft, BACK_TO_DURATION)],
            ]
        )
    elif step == STEP_MERGE:
        lines.append(f"💵 {fmt_sgd(data['amount_cents'])}, paid by {mention_html(payer)}")
        span = fmt_session(merge_into.starts_at, merge_into.ends_at)
        lines.append(f"There's already a session on {span}. Is this court part of it?")
        markup = InlineKeyboardMarkup(
            [
                [_button(f"➕ Add to {_times(merge_into)}", draft, MERGE, str(merge_into.id))],
                [_button("Separate session", draft, SEPARATE)],
            ]
        )
    else:
        lines.append(f"💵 {fmt_sgd(data['amount_cents'])}, paid by {mention_html(payer)}")
        if merge_into is not None:
            lines.append(f"➕ Part of the {_times(merge_into)} session")
        lines.append("Is this right?")
        markup = InlineKeyboardMarkup(
            [
                [
                    _button("✅ Confirm", draft, CONFIRM),
                    _button("✏️ Change price", draft, BACK_TO_DURATION),
                ]
            ]
        )
    rows = [*markup.inline_keyboard, [_button("✖ Cancel", draft, CANCEL)]]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def render_record(payer: Member, starts_at: datetime, minutes: int, amount_cents: int) -> str:
    """The permanent booking record the card becomes once confirmed (FR-BK-8)."""
    ends_at = starts_at + timedelta(minutes=minutes)
    return (
        f"✅ <b>Court booked</b> by {mention_html(payer)}\n"
        f"📅 {fmt_session(starts_at, ends_at)} ({fmt_hours(minutes)}) · {fmt_sgd(amount_cents)}"
    )


def card_note(starts_at: datetime, days_before: int, today: date) -> str:
    due = card_due_date(starts_at, days_before)
    if due <= today:
        return "🗳 The attendance card is going up now."
    return f"🗳 Attendance card goes up on {fmt_date(due)}."


def _times(session: Session) -> str:
    return fmt_session(session.starts_at, session.ends_at).split(", ", 1)[1]


def _button(text: str, draft: Draft, action: str, value: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=encode(CALLBACK_KIND, draft.id, action, value))


async def refresh_wizard(
    context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft
) -> None:
    payer = await db.get(Member, draft.data["payer_member_id"])
    session_id = draft.data.get("merge_candidate_id") or draft.data.get("merge_session_id")
    merge_into = await db.get(Session, session_id) if session_id else None
    text, markup = render(draft, payer, merge_into)
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
        if at_sgt(parse_day(draft.data["day"]), int(value)) <= utc_now():
            return "That time has passed."
        changes = {"hour": int(value), "step": STEP_DURATION}
    elif action == BACK_TO_HOUR:
        changes = {"step": STEP_HOUR, "minutes": None}
    elif action == PICK_DURATION:
        changes = await _price_step(context, db, int(value))
    elif action == BACK_TO_DURATION:
        changes = {
            "step": STEP_DURATION,
            "amount_cents": None,
            "merge_candidate_id": None,
            "merge_session_id": None,
        }
    elif action == PICK_PRICE:
        changes = await _after_price(db, draft, int(value))
    elif action == MERGE:
        changes = {"merge_session_id": int(value), "merge_candidate_id": None, "step": STEP_CONFIRM}
    elif action == SEPARATE:
        changes = {"merge_candidate_id": None, "step": STEP_CONFIRM}
    elif action == OTHER_PRICE:
        await prompts.ask(
            context.bot,
            draft,
            "how much did you pay for the court? (e.g. 14.80)",
            owner=update.effective_user,
            field="amount",
        )
        return None
    elif action == CONFIRM:
        return await _confirm(update, context, db, draft)
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
    await refresh_wizard(context, db, draft)
    return None


async def _price_step(
    context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, minutes: int
) -> dict[str, Any]:
    """Move to the price step, suggesting the last hourly rate × hours (FR-BK-6)."""
    rate = await last_hourly_rate(db, settings_of(context).default_court_rate_cents)
    suggested = (rate * minutes + 30) // 60
    return {"minutes": minutes, "suggested_cents": suggested, "step": STEP_PRICE}


def _booking_times(draft: Draft) -> tuple[datetime, datetime]:
    starts_at = at_sgt(parse_day(draft.data["day"]), draft.data["hour"])
    return starts_at, starts_at + timedelta(minutes=draft.data["minutes"])


async def _after_price(db: AsyncSession, draft: Draft, amount_cents: int) -> dict[str, Any]:
    """Ask whether to join a same-day session that overlaps or touches this booking
    (FR-BK-7); otherwise go straight to confirm."""
    candidate = await find_mergeable_session(db, *_booking_times(draft))
    if candidate is None:
        return {"amount_cents": amount_cents, "step": STEP_CONFIRM}
    return {"amount_cents": amount_cents, "merge_candidate_id": candidate.id, "step": STEP_MERGE}


async def _confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft
) -> str | None:
    data = draft.data
    starts_at, ends_at = _booking_times(draft)
    now = utc_now()
    if starts_at <= now:
        return "That time has passed. Change the date or time first."
    merge_into = None
    if data.get("merge_session_id"):
        # Only join if it can still be joined, e.g. it wasn't cancelled meanwhile.
        candidate = await find_mergeable_session(db, starts_at, ends_at)
        if candidate is not None and candidate.id == data["merge_session_id"]:
            merge_into = candidate
    payer = await db.get(Member, data["payer_member_id"])
    booking = await record_booking(
        db,
        payer_id=payer.id,
        starts_at=starts_at,
        minutes=data["minutes"],
        amount_cents=data["amount_cents"],
        photo_file_id=data.get("photo_file_id"),
        record_chat_id=draft.chat_id,
        record_message_id=draft.message_id,
        session_id=merge_into.id if merge_into is not None else None,
    )
    await delete_draft(db, draft)
    session = await db.get(Session, booking.session_id)
    if merge_into is not None:
        await refresh_card(context.bot, db, session.id)
    lines = [render_record(payer, starts_at, data["minutes"], data["amount_cents"])]
    if merge_into is not None:
        lines.append(f"➕ Added to the {fmt_session(session.starts_at, session.ends_at)} session")
    lines.append(
        card_note(session.starts_at, settings_of(context).reminder_days_before, sgt_date(now))
    )
    await update.callback_query.edit_message_text(
        "\n".join(lines), reply_markup=cancel_markup(booking.id)
    )
    await post_card_if_due(context.bot, db, session, settings_of(context), now)
    return None


def cancel_markup(booking_id: int) -> InlineKeyboardMarkup:
    """The button on a booking record that lets its submitter or the admin cancel it."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🗑 Cancel booking", callback_data=f"bkc:{booking_id}")]]
    )


@prompts.on_reply(KIND)
async def on_typed_answer(
    _update: Update, context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft, text: str
) -> None:
    awaiting = draft.data.get("awaiting")
    try:
        if awaiting == "minutes":
            update_data(draft, **await _price_step(context, db, parse_hours(text)))
        elif awaiting == "amount":
            update_data(draft, **await _after_price(db, draft, parse_amount(text)))
    except ValueError as error:
        raise prompts.PromptError(str(error)) from None
    await refresh_wizard(context, db, draft)


def register(application: Application) -> None:
    application.add_handler(MessageHandler(filters.ChatType.GROUPS & BOOK_PHOTO, start_booking))
    application.add_handler(
        CommandHandler("book", book_without_photo, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(CallbackQueryHandler(on_button, pattern=rf"^{CALLBACK_KIND}:\d+:"))
