"""The booking wizard (FR-BK-1 to FR-BK-9, FR-BK-12).

A member sends /court in the group and replies to the bot's prompt with their booking
screenshot, or sends the screenshot with the caption /court. The bot reads the date,
time, venue and courts off it (OCR) and replies with one card, edited in place: it
asks for anything it couldn't read, then shows the booking to confirm or edit. Only
the submitter can operate it (FR-BK-2); the sender is the payer.

Once confirmed, the bot posts the screenshot captioned with the booking and pins it.
That post is the booking's record; the card and the member's own copy are deleted.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageEntity,
    Update,
)
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from badminton.bot import prompts
from badminton.bot.commands import BOOKING_COMMAND, OLD_BOOKING_COMMAND
from badminton.bot.context import db_session, ocr_of, settings_of
from badminton.bot.handlers import guard
from badminton.bot.handlers.guard import caption_command, is_booking_photo
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
from badminton.db.models import Booking, Draft, Member, Session
from badminton.domain.money import fmt_sgd, parse_amount
from badminton.domain.screenshot import fmt_courts, parse_courts, parse_venue, read_booking
from badminton.domain.timeutil import (
    at_sgt,
    card_due_date,
    fmt_date,
    fmt_hours,
    fmt_session,
    parse_hours,
    sgt_date,
    to_sgt,
    utc_now,
)
from badminton.services.attendance import post_card_if_due, refresh_card
from badminton.services.drafts import (
    create_draft,
    delete_draft,
    delete_message_quietly,
    get_draft,
    show_draft,
    update_data,
)
from badminton.services.members import mention_html, upsert_from_telegram
from badminton.services.notify import pin, unpin
from badminton.services.repo import last_hourly_rate
from badminton.services.sessions import find_mergeable_session, record_booking

KIND = "booking"
CALLBACK_KIND = "bk"

PICK_DURATION = "dur"
OTHER_DURATION = "durx"
BACK_TO_HOUR = "bkh"
CONFIRM = "ok"
EDIT = "e"
EDIT_DATE = "ed"
EDIT_TIME = "et"
EDIT_HOURS = "eh"
EDIT_VENUE = "ev"
EDIT_COURTS = "ec"
EDIT_PRICE = "ep"
BACK_TO_EDIT = "be"
SUMMARY = "sum"
MERGE = "mg"
SEPARATE = "sep"
CANCEL = "x"

STEP_PHOTO = "photo"
STEP_DATE = "date"
STEP_HOUR = "hour"
STEP_DURATION = "duration"
STEP_MERGE = "merge"
STEP_CONFIRM = "confirm"
STEP_EDIT = "edit"

# Buttons that open a reply prompt, and what they ask: (question, draft field).
QUESTIONS = {
    OTHER_DURATION: ("how many hours is it booked for? (e.g. 3 or 1.5)", "minutes"),
    EDIT_VENUE: ("what's the venue? (e.g. Hougang Sport Hall)", "venue"),
    EDIT_COURTS: ("which court is it? (e.g. 11, or 11, 12 for two)", "courts"),
    EDIT_PRICE: ("how much did you pay for the court? (e.g. 14.80)", "amount_cents"),
}
PARSERS = {
    "minutes": parse_hours,
    "venue": parse_venue,
    "courts": parse_courts,
    "amount_cents": parse_amount,
}
EDIT_STEPS = {EDIT_DATE: STEP_DATE, EDIT_TIME: STEP_HOUR, EDIT_HOURS: STEP_DURATION}
# Buttons only shown once the date, time and hours are all known.
NEEDS_SCHEDULE = {
    CONFIRM,
    EDIT,
    BACK_TO_EDIT,
    SUMMARY,
    MERGE,
    SEPARATE,
    *EDIT_STEPS,
    EDIT_VENUE,
    EDIT_COURTS,
    EDIT_PRICE,
}

ASK_SCREENSHOT = "send your booking screenshot as a reply to this message."
EXPIRED = f"This booking form has expired. Send /{BOOKING_COMMAND} again."
IN_THE_GROUP = f"📸 Send /{BOOKING_COMMAND} in the group, then your booking screenshot."
MOVED = (
    f"📸 /{OLD_BOOKING_COMMAND} is now /{BOOKING_COMMAND}: send /{BOOKING_COMMAND} in the group, "
    "then your booking screenshot."
)
MERGE_FIRST = "There's now a session next to this one: is it part of it?"
UNREAD = "🔍 I couldn't work out the date and time from the screenshot."
NOT_READ = "📍 Venue and court not read: add them with ✏️ Edit"

logger = logging.getLogger(__name__)


# Rendering --------------------------------------------------------------------


def booking_lines(
    payer: Member,
    starts_at: datetime,
    minutes: int,
    amount_cents: int,
    venue: str | None,
    courts: str | None,
) -> list[str]:
    """The booking as the member confirms it, and as its record shows it."""
    ends_at = starts_at + timedelta(minutes=minutes)
    lines = [f"📅 {fmt_session(starts_at, ends_at)} ({fmt_hours(minutes)})"]
    if where := _where(venue, courts):
        lines.append(f"📍 {where}")
    lines.append(f"💵 {fmt_sgd(amount_cents)}, paid by {mention_html(payer)}")
    return lines


def _where(venue: str | None, courts: str | None) -> str | None:
    """``Hougang Sport Hall · Court 11``, or as much of it as is known."""
    parts = [html.escape(venue)] if venue else []
    if courts:
        parts.append(html.escape(fmt_courts(courts)))
    return " · ".join(parts) or None


def render(
    draft: Draft, payer: Member, merge_into: Session | None = None
) -> tuple[str, InlineKeyboardMarkup]:
    """The wizard card for the draft's current step. ``merge_into`` is the session the
    booking may join (merge step) or will join (confirm step)."""
    data = draft.data
    step = data["step"]
    editing = _missing_step(data) is None  # otherwise it's asking for what OCR missed
    lines = _summary_lines(data, payer) if editing else _progress_lines(data)
    back = [_button("‹ Back", draft, BACK_TO_EDIT)]
    rows: list[Any]
    if step == STEP_DATE:
        lines.append("Which day is the court booked for?")
        today = sgt_date(utc_now())
        month = parse_month(data["month"]) if "month" in data else today
        rows = [*calendar_keyboard(CALLBACK_KIND, draft.id, month, today).inline_keyboard]
        if editing:
            rows.append(back)
    elif step == STEP_HOUR:
        lines.append("What time does it start?")
        rows = [*hour_keyboard(CALLBACK_KIND, draft.id).inline_keyboard]
        if editing:
            rows[-1] = back  # instead of "‹ Change date", which starts the date over
    elif step == STEP_DURATION:
        lines.append("How long is it booked for?")
        rows = [
            [
                _button("1h", draft, PICK_DURATION, "60"),
                _button("2h", draft, PICK_DURATION, "120"),
                _button("Other…", draft, OTHER_DURATION),
            ],
            back if editing else [_button("‹ Change time", draft, BACK_TO_HOUR)],
        ]
    elif step == STEP_MERGE:
        span = fmt_session(merge_into.starts_at, merge_into.ends_at)
        lines.append(f"There's already a session on {span}. Is this court part of it?")
        rows = [
            [_button(f"➕ Add to {_times(merge_into)}", draft, MERGE, str(merge_into.id))],
            [_button("Separate session", draft, SEPARATE)],
            [_button("✏️ Edit", draft, EDIT)],
        ]
    elif step == STEP_EDIT:
        lines.append("What do you want to change?")
        rows = [
            [
                _button("📅 Date", draft, EDIT_DATE),
                _button("🕐 Time", draft, EDIT_TIME),
                _button("⏱ Hours", draft, EDIT_HOURS),
            ],
            [
                _button("📍 Venue", draft, EDIT_VENUE),
                _button("🔢 Courts", draft, EDIT_COURTS),
                _button("💵 Price", draft, EDIT_PRICE),
            ],
            [_button("‹ Back", draft, SUMMARY)],
        ]
    else:
        if merge_into is not None:
            lines.append(f"➕ Part of the {_times(merge_into)} session")
        lines.append("Is this right?")
        rows = [[_button("✅ Confirm", draft, CONFIRM), _button("✏️ Edit", draft, EDIT)]]
    rows.append([_button("✖ Cancel", draft, CANCEL)])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _summary_lines(data: dict[str, Any], payer: Member) -> list[str]:
    starts_at, _ = _booking_times(data)
    venue, courts = data.get("venue"), data.get("courts")
    lines = booking_lines(payer, starts_at, data["minutes"], _price(data), venue, courts)
    if not venue and not courts:
        lines.insert(1, NOT_READ)
    if starts_at <= utc_now():
        lines.append("⚠️ That time has passed.")
    return lines


def _progress_lines(data: dict[str, Any]) -> list[str]:
    """What's known while asking for the date and time the screenshot didn't give."""
    lines = [UNREAD]
    if "day" in data:
        day = fmt_date(parse_day(data["day"]))
        lines.append(f"📅 {day}, {data['hour']:02d}:00" if "hour" in data else f"📅 {day}")
    if where := _where(data.get("venue"), data.get("courts")):
        lines.append(f"📍 {where}")
    return lines


def render_record(payer: Member, booking: Booking) -> str:
    """The booking as its record shows it: the caption on the posted screenshot."""
    return "\n".join(
        booking_lines(
            payer,
            booking.starts_at,
            booking.minutes,
            booking.amount_cents,
            booking.venue,
            booking.courts,
        )
    )


async def refresh_record(bot: Bot, db: AsyncSession, booking: Booking, note: str) -> None:
    """Re-render a booking's record after it was changed or cancelled. ``note`` says what
    happened. A cancelled booking's record is unpinned (FR-BK-10)."""
    if booking.record_message_id is None:
        return
    payer = await db.get(Member, booking.payer_member_id)
    record = render_record(payer, booking)
    if booking.cancelled_at is None:
        text, markup = f"{record}\n{note}", cancel_markup(booking.id)
    else:
        text, markup = f"🗑 <s>{record}</s>\n{note}", None
    where = {"chat_id": booking.record_chat_id, "message_id": booking.record_message_id}
    try:
        await bot.edit_message_caption(caption=text, reply_markup=markup, **where)
    except BadRequest:
        # Records posted before bookings were read from screenshots are text messages.
        with contextlib.suppress(BadRequest):  # or the record was deleted
            await bot.edit_message_text(text, reply_markup=markup, **where)
    if booking.cancelled_at is not None:
        await unpin(bot, booking.record_chat_id, booking.record_message_id)


def card_note(starts_at: datetime, days_before: int, today: date) -> str:
    due = card_due_date(starts_at, days_before)
    if due <= today:
        return "🗳 The attendance card is going up now."
    return f"🗳 Attendance card goes up on {fmt_date(due)}."


def _times(session: Session) -> str:
    return fmt_session(session.starts_at, session.ends_at).split(", ", 1)[1]


def _button(text: str, draft: Draft, action: str, value: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=encode(CALLBACK_KIND, draft.id, action, value))


async def _card(db: AsyncSession, draft: Draft) -> tuple[str, InlineKeyboardMarkup]:
    payer = await db.get(Member, draft.data["payer_member_id"])
    session_id = draft.data.get("merge_candidate_id") or draft.data.get("merge_session_id")
    merge_into = await db.get(Session, session_id) if session_id else None
    return render(draft, payer, merge_into)


async def refresh_wizard(
    context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft
) -> None:
    text, markup = await _card(db, draft)
    await show_draft(context.bot, draft, text, markup)


# Handlers ---------------------------------------------------------------------


async def ask_for_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/court: ask for the booking screenshot, as a reply to the prompt."""
    message = update.effective_message
    async with db_session(context) as db:
        payer = await upsert_from_telegram(db, update.effective_user)
        await _close_waiting(context.bot, db, message.chat_id, update.effective_user.id)
        draft = await create_draft(
            db,
            kind=KIND,
            chat_id=message.chat_id,
            user_id=update.effective_user.id,
            now=utc_now(),
            data={"step": STEP_PHOTO, "payer_member_id": payer.id},
        )
        await prompts.ask(
            context.bot, draft, ASK_SCREENSHOT, owner=update.effective_user, field="photo"
        )
        await db.commit()


async def on_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A screenshot replying to the sender's /court prompt, or captioned /court."""
    message = update.effective_message
    user = update.effective_user
    async with db_session(context) as db:
        draft = await _prompt_draft(db, message, user.id)
        if draft is None and not is_booking_photo(message, context.bot.username):
            if caption_command(message, context.bot.username) == OLD_BOOKING_COMMAND:
                await point_to_new_command(update, context)
            else:
                await guard.delete_unless_exempt(update, context)
            return
        if not message.photo:  # an image sent as a file
            await delete_message_quietly(context.bot, message.chat_id, message.message_id)
            await prompts.ask(
                context.bot,
                draft,
                ASK_SCREENSHOT,
                owner=user,
                field="photo",
                error="Send it as a photo, not as a file.",
            )
            await db.commit()
            return
        payer = await upsert_from_telegram(db, user)
        if draft is not None and draft.expires_at <= utc_now():
            # The prompt outlived its form until the tick's cleanup: start a new one.
            await delete_draft(context.bot, db, draft)
            draft = None
        if draft is None:
            await _close_waiting(context.bot, db, message.chat_id, user.id)
            draft = await create_draft(
                db,
                kind=KIND,
                chat_id=message.chat_id,
                user_id=user.id,
                now=utc_now(),
                data={"step": STEP_PHOTO, "payer_member_id": payer.id},
            )
        await _read_screenshot(context, db, draft, message)
        # Only now the card is up: had anything failed, the member could answer again.
        await prompts.dismiss(context.bot, draft)
        await db.commit()


async def point_to_new_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/book, as before T44, as text or a caption: say what replaced it. In the group the
    message is then removed, like any other that isn't a command the bot handles."""
    await context.bot.send_message(update.effective_chat.id, MOVED)
    await guard.delete_unless_exempt(update, context)


async def in_the_group(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(IN_THE_GROUP)


async def _prompt_draft(db: AsyncSession, message: Message, user_id: int) -> Draft | None:
    """The sender's booking draft whose screenshot prompt the message replies to. It may
    have expired: the prompt stays up until the tick cleans it away."""
    if message.reply_to_message is None:
        return None
    draft = await db.scalar(
        select(Draft)
        .where(
            Draft.kind == KIND,
            Draft.chat_id == message.chat_id,
            Draft.user_id == user_id,
            Draft.prompt_message_id == message.reply_to_message.message_id,
        )
        .with_for_update()  # one screenshot at a time, as for typed answers
    )
    return draft if draft is not None and draft.data.get("awaiting") == "photo" else None


async def _close_waiting(bot: Bot, db: AsyncSession, chat_id: int, user_id: int) -> None:
    """Close the member's forms still waiting for a screenshot, and their prompts: the
    booking they start now replaces them."""
    waiting = await db.scalars(
        select(Draft).where(
            Draft.kind == KIND,
            Draft.chat_id == chat_id,
            Draft.user_id == user_id,
            Draft.data["awaiting"].astext == "photo",
        )
    )
    for draft in waiting.all():
        await delete_draft(bot, db, draft)


async def _read_screenshot(
    context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft, message: Message
) -> None:
    """Fill the draft in from the screenshot and reply to it with the wizard card."""
    file_id = message.photo[-1].file_id
    found = read_booking(await _screenshot_text(context, file_id), sgt_date(utc_now()))
    update_data(
        draft,
        photo_file_id=file_id,
        owned_message_ids=[message.message_id],  # the record will carry the screenshot
        venue=found.venue,
        courts=found.courts,
    )
    if found.starts_at is None:
        update_data(draft, step=STEP_DATE)
    else:
        day = sgt_date(found.starts_at).strftime("%Y%m%d")
        await _reschedule(
            context,
            db,
            draft,
            day=day,
            month=day[:6],
            hour=to_sgt(found.starts_at).hour,
            minutes=found.minutes,
            court_minutes=found.court_minutes,
        )
    text, markup = await _card(db, draft)
    card = await message.reply_text(text, reply_markup=markup)
    draft.message_id = card.message_id


async def _screenshot_text(context: ContextTypes.DEFAULT_TYPE, file_id: str) -> str:
    """The screenshot's text, or ``""`` if it can't be had: then the member types it in."""
    try:
        file = await context.bot.get_file(file_id)
        image = bytes(await file.download_as_bytearray())
    except TelegramError as error:
        logger.warning("Could not download the booking screenshot: %s", error)
        return ""
    return await asyncio.to_thread(ocr_of(context), image)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    callback = decode(query.data)
    async with db_session(context) as db:
        draft = await get_draft(db, callback.draft_id, now=utc_now(), lock=True)
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
    if action == NOOP:
        return None
    if action in NEEDS_SCHEDULE and _missing_step(draft.data) is not None:
        return None  # a stale button
    if action not in QUESTIONS:
        await prompts.dismiss(context.bot, draft)
    if action == CANCEL:
        await _cancel(update, context, db, draft)
        return None
    if action == CONFIRM:
        return await _confirm(update, context, db, draft)
    if action in QUESTIONS:
        question, field = QUESTIONS[action]
        await prompts.ask(context.bot, draft, question, owner=update.effective_user, field=field)
        return None
    if action == SHOW_MONTH:
        update_data(draft, month=value)
    elif action == PICK_DATE:
        if parse_day(value) < sgt_date(utc_now()):
            return "That date has passed."
        await _reschedule(context, db, draft, day=value, month=value[:6])
    elif action == BACK:
        update_data(draft, step=STEP_DATE, hour=None, minutes=None)
    elif action == PICK_HOUR:
        if "day" not in draft.data:
            return None
        if at_sgt(parse_day(draft.data["day"]), int(value)) <= utc_now():
            return "That time has passed."
        await _reschedule(context, db, draft, hour=int(value))
    elif action == BACK_TO_HOUR:
        update_data(draft, step=STEP_HOUR, minutes=None)
    elif action == PICK_DURATION:
        await _reschedule(context, db, draft, minutes=int(value))
    elif action in (EDIT, BACK_TO_EDIT):
        update_data(draft, step=STEP_EDIT)
    elif action in EDIT_STEPS:
        update_data(draft, step=EDIT_STEPS[action])
    elif action == SUMMARY:
        update_data(draft, **await _summary(db, draft))
    elif action == MERGE:
        update_data(draft, merge_session_id=int(value), merge_candidate_id=None, step=STEP_CONFIRM)
    elif action == SEPARATE:
        update_data(draft, separate=True, merge_candidate_id=None, step=STEP_CONFIRM)
    else:  # a button of an older version of the form, left open over an update
        await _expire(context, db, draft)
        return EXPIRED
    await refresh_wizard(context, db, draft)
    return None


def _missing_step(data: dict[str, Any]) -> str | None:
    """The step asking for the first of date, time and hours not known yet."""
    for key, step in (("day", STEP_DATE), ("hour", STEP_HOUR), ("minutes", STEP_DURATION)):
        if key not in data:
            return step
    return None


async def _reschedule(
    context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft, **changes: Any
) -> None:
    """Change the date, time or hours, then ask for whatever is still missing or show the
    booking. It may now join a different session, so that question is asked again. Hours
    the member picks are taken as one court at a time: the screenshot's court time, if
    any, no longer applies."""
    if "minutes" in changes:
        changes.setdefault("court_minutes", None)
    update_data(draft, **changes, merge_candidate_id=None, merge_session_id=None, separate=None)
    if "minutes" in changes:
        update_data(draft, suggested_cents=await _suggested_price(context, db, draft.data))
    if (step := _missing_step(draft.data)) is not None:
        update_data(draft, step=step)
    else:
        update_data(draft, **await _summary(db, draft))


async def _suggested_price(
    context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, data: dict[str, Any]
) -> int:
    """The last hourly rate × the court time: the screenshot's slots, or else the hours,
    one court at a time (FR-BK-6)."""
    rate = await last_hourly_rate(db, settings_of(context).default_court_rate_cents)
    court_minutes = data.get("court_minutes") or data["minutes"]
    return (rate * court_minutes + 30) // 60


def _price(data: dict[str, Any]) -> int:
    """The price typed in, else the suggested one."""
    return data.get("amount_cents", data["suggested_cents"])


def _booking_times(data: dict[str, Any]) -> tuple[datetime, datetime]:
    starts_at = at_sgt(parse_day(data["day"]), data["hour"])
    return starts_at, starts_at + timedelta(minutes=data["minutes"])


async def _summary(db: AsyncSession, draft: Draft) -> dict[str, Any]:
    """Show the booking to confirm. First ask whether it joins a same-day session that
    overlaps or touches it (FR-BK-7), unless that was answered already."""
    if draft.data.get("merge_session_id") or draft.data.get("separate"):
        return {"step": STEP_CONFIRM}
    candidate = await find_mergeable_session(db, *_booking_times(draft.data))
    if candidate is None:
        return {"step": STEP_CONFIRM, "merge_candidate_id": None}
    return {"step": STEP_MERGE, "merge_candidate_id": candidate.id}


async def _cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft
) -> None:
    await delete_draft(context.bot, db, draft)  # and the member's screenshot
    await update.callback_query.edit_message_text("✖ Booking cancelled.")


async def _expire(context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft) -> None:
    """End a form this version can't carry on, e.g. one left open over an update."""
    await delete_draft(context.bot, db, draft)
    with contextlib.suppress(BadRequest):  # the card is gone
        await context.bot.edit_message_text(
            EXPIRED, chat_id=draft.chat_id, message_id=draft.message_id
        )


async def _confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft
) -> str | None:
    data = draft.data
    starts_at, ends_at = _booking_times(data)
    now = utc_now()
    if starts_at <= now:
        return "That time has passed. Change the date or time first."
    if not data.get("merge_session_id") and not data.get("separate"):
        # Someone may have booked a session to join since this card was shown (FR-BK-7).
        candidate = await find_mergeable_session(db, starts_at, ends_at)
        if candidate is not None:
            update_data(draft, step=STEP_MERGE, merge_candidate_id=candidate.id)
            await refresh_wizard(context, db, draft)
            return MERGE_FIRST
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
        amount_cents=_price(data),
        photo_file_id=data["photo_file_id"],
        venue=data.get("venue"),
        courts=data.get("courts"),
        record_chat_id=draft.chat_id,
        record_message_id=None,
        session_id=merge_into.id if merge_into is not None else None,
    )
    session = await db.get(Session, booking.session_id)
    lines = [render_record(payer, booking)]
    if merge_into is not None:
        lines.append(f"➕ Added to the {fmt_session(session.starts_at, session.ends_at)} session")
    lines.append(
        card_note(session.starts_at, settings_of(context).reminder_days_before, sgt_date(now))
    )
    # The record: the screenshot, captioned with the booking (FR-BK-8).
    record = await context.bot.send_photo(
        draft.chat_id,
        data["photo_file_id"],
        caption="\n".join(lines),
        reply_markup=cancel_markup(booking.id),
    )
    booking.record_message_id = record.message_id
    await delete_draft(context.bot, db, draft)  # and the member's screenshot: the record has it
    await db.commit()

    # The attendance card first; pinning and removing the wizard are only tidying up.
    if merge_into is not None:
        await refresh_card(context.bot, db, session.id)
    await post_card_if_due(context.bot, db, session, settings_of(context), now)
    await pin(context.bot, record.chat_id, record.message_id)
    await delete_message_quietly(context.bot, draft.chat_id, draft.message_id)
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
    if awaiting != "photo" and awaiting not in PARSERS:  # asked by an older version of the form
        await _expire(context, db, draft)
        return
    try:
        if awaiting == "photo":
            raise ValueError("Send your booking screenshot as a photo.")
        value = PARSERS[awaiting](text)
    except ValueError as error:
        raise prompts.PromptError(str(error)) from None
    if awaiting == "minutes":
        await _reschedule(context, db, draft, minutes=value)
    else:
        update_data(draft, **{awaiting: value})
        update_data(draft, **await _summary(db, draft))
    await refresh_wizard(context, db, draft)


def register(application: Application) -> None:
    # A captioned command is checked in the handler, as CommandHandler checks text commands.
    captioned = filters.PHOTO & filters.CaptionEntity(MessageEntity.BOT_COMMAND)
    replied = filters.REPLY & (filters.PHOTO | filters.Document.IMAGE)
    application.add_handler(
        MessageHandler(filters.ChatType.GROUPS & (captioned | replied), on_screenshot)
    )
    application.add_handler(
        CommandHandler(BOOKING_COMMAND, ask_for_screenshot, filters=filters.ChatType.GROUPS)
    )
    application.add_handler(
        CommandHandler(BOOKING_COMMAND, in_the_group, filters=filters.ChatType.PRIVATE)
    )
    application.add_handler(CommandHandler(OLD_BOOKING_COMMAND, point_to_new_command))
    application.add_handler(CallbackQueryHandler(on_button, pattern=rf"^{CALLBACK_KIND}:\d+:"))
