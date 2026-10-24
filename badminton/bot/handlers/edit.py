"""/edit: the admin corrects a session (FR-ADM-2, FR-ADM-3, FR-ADM-5).

Flow: recent-session picker → session menu → attendance toggles, a booking's price,
payer or hours, or cancelling the session. Every saved change is announced in the
group with the recalculated split, audited, and the session's card is refreshed.
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
from badminton.bot.handlers.booking import refresh_record
from badminton.bot.keyboards import decode, encode
from badminton.db.models import Booking, Draft, Member, Session
from badminton.domain.money import fmt_sgd, parse_amount
from badminton.domain.timeutil import (
    fmt_date,
    fmt_hours,
    fmt_session,
    fmt_time,
    parse_hours,
    sgt_date,
    utc_now,
)
from badminton.services import audit
from badminton.services.attendance import (
    IN,
    OUT,
    CardView,
    close_cancelled_card,
    load_card_view,
    refresh_card,
    set_attendance,
)
from badminton.services.drafts import (
    create_draft,
    delete_draft,
    get_draft,
    show_draft,
    update_data,
)
from badminton.services.members import active_members, upsert_from_telegram
from badminton.services.notify import announce
from badminton.services.sessions import cancel_session, recompute_span

KIND = "edit"
CALLBACK_KIND = "ed"
RECENT_SESSIONS = 8  # that have started: the ones whose attendance may need fixing
UPCOMING_SESSIONS = 4

PICK_SESSION = "s"
SESSIONS = "list"
ATTENDANCE = "att"
TOGGLE = "t"
SAVE = "save"
CLOSE = "x"
BOOKINGS = "bks"
BOOKING = "b"
PRICE = "pr"
PAYER = "py"
SET_PAYER = "sp"
HOURS = "hr"
SET_HOURS = "sh"
OTHER_HOURS = "ho"
CANCEL_SESSION = "cs"
CONFIRM_CANCEL = "cy"


def _button(text: str, draft: Draft, action: str, value: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=encode(CALLBACK_KIND, draft.id, action, value))


def _label(session: Session) -> str:
    when = f"{fmt_date(sgt_date(session.starts_at))} {fmt_time(session.starts_at)}"
    return f"{when} 🔒" if session.locked_at else when


async def render(db: AsyncSession, draft: Draft) -> tuple[str, InlineKeyboardMarkup]:
    data = draft.data
    screen = data.get("screen", SESSIONS)
    if screen == SESSIONS:
        now = utc_now()
        scheduled = select(Session).where(Session.status == "scheduled")
        upcoming = await db.scalars(
            scheduled.where(Session.starts_at > now)
            .order_by(Session.starts_at)
            .limit(UPCOMING_SESSIONS)
        )
        recent = await db.scalars(
            scheduled.where(Session.starts_at <= now)
            .order_by(Session.starts_at.desc())
            .limit(RECENT_SESSIONS)
        )
        sessions = [*reversed(upcoming.all()), *recent]  # newest first
        rows = [[_button(_label(s), draft, PICK_SESSION, str(s.id))] for s in sessions]
        text = "✏️ <b>Edit a session</b>\nWhich one?" if rows else "There are no sessions to edit."
        return text, InlineKeyboardMarkup([*rows, [_button("✖ Close", draft, CLOSE)]])

    session = await db.get(Session, data["session_id"])
    title = f"✏️ <b>{fmt_session(session.starts_at, session.ends_at)}</b>"
    if screen == ATTENDANCE:
        ins = set(data["ins"])
        members = await active_members(db)
        shown = members + [m for m in await _members(db, ins) if not m.active]
        buttons = [
            _button(f"{'✅' if m.id in ins else '⬜'} {m.display_name}", draft, TOGGLE, str(m.id))
            for m in shown
        ]
        rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
        rows.append(
            [
                _button("💾 Save", draft, SAVE),
                _button("‹ Back", draft, PICK_SESSION, str(session.id)),
            ]
        )
        return f"{title}\nWho played? Tap to toggle, then Save.", InlineKeyboardMarkup(rows)

    if screen == BOOKINGS:
        rows = [
            [_button(_booking_label(booking, names), draft, BOOKING, str(booking.id))]
            for booking, names in await _active_bookings(db, session)
        ]
        rows.append([_button("‹ Back", draft, PICK_SESSION, str(session.id))])
        return f"{title}\nWhich booking?", InlineKeyboardMarkup(rows)

    if screen in (BOOKING, PAYER, HOURS):
        booking = await db.get(Booking, data["booking_id"])
        payer = await db.get(Member, booking.payer_member_id)
        title += (
            f"\n{html.escape(payer.display_name)}'s booking: {fmt_time(booking.starts_at)}, "
            f"{fmt_hours(booking.minutes)}, {fmt_sgd(booking.amount_cents)}"
        )
        back = _button("‹ Back", draft, BOOKING, str(booking.id))
        if screen == PAYER:
            buttons = [
                _button(m.display_name, draft, SET_PAYER, str(m.id))
                for m in await active_members(db)
            ]
            rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)] + [[back]]
            return f"{title}\nWho paid for it?", InlineKeyboardMarkup(rows)
        if screen == HOURS:
            rows = [
                [
                    _button("1h", draft, SET_HOURS, "60"),
                    _button("2h", draft, SET_HOURS, "120"),
                    _button("Other…", draft, OTHER_HOURS),
                ],
                [back],
            ]
            return f"{title}\nHow long is it booked for?", InlineKeyboardMarkup(rows)
        rows = [
            [
                _button("💵 Price", draft, PRICE),
                _button("👤 Payer", draft, PAYER),
                _button("⏱ Hours", draft, HOURS),
            ],
            [_button("‹ Bookings", draft, BOOKINGS)],
        ]
        return f"{title}\nWhat should change?", InlineKeyboardMarkup(rows)

    if screen == CANCEL_SESSION:
        rows = [
            [_button("Yes, cancel the session", draft, CONFIRM_CANCEL)],
            [_button("‹ Keep it", draft, PICK_SESSION, str(session.id))],
        ]
        text = f"{title}\nCancel this session and all its bookings? Nobody is charged for it."
        return text, InlineKeyboardMarkup(rows)

    rows = [
        [_button("👥 Attendance", draft, ATTENDANCE), _button("💵 Bookings", draft, BOOKINGS)],
        [_button("🗑 Cancel session", draft, CANCEL_SESSION)],
        [_button("‹ Sessions", draft, SESSIONS), _button("✖ Close", draft, CLOSE)],
    ]
    return f"{title}\nWhat do you want to change?", InlineKeyboardMarkup(rows)


async def _active_bookings(
    db: AsyncSession, session: Session
) -> list[tuple[Booking, dict[int, str]]]:
    bookings = await db.scalars(
        select(Booking)
        .where(Booking.session_id == session.id, Booking.cancelled_at.is_(None))
        .order_by(Booking.starts_at, Booking.id)
    )
    names = {m.id: m.display_name for m in await db.scalars(select(Member))}
    return [(booking, names) for booking in bookings]


def _booking_label(booking: Booking, names: dict[int, str]) -> str:
    return (
        f"{names[booking.payer_member_id]} · {fmt_time(booking.starts_at)} · "
        f"{fmt_hours(booking.minutes)} · {fmt_sgd(booking.amount_cents)}"
    )


async def _members(db: AsyncSession, ids: set[int]) -> list[Member]:
    if not ids:
        return []
    return list(await db.scalars(select(Member).where(Member.id.in_(ids)).order_by(Member.id)))


async def _current_ins(db: AsyncSession, session_id: int) -> list[int]:
    view = await load_card_view(db, session_id)
    return sorted(a.member_id for a in view.session.attendance if a.status == IN)


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
            data={"screen": SESSIONS},
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
            await query.answer("This form has expired. Use /edit again.", show_alert=True)
            return
        await query.answer()
        action, value = callback.action, callback.value
        if action not in (PRICE, OTHER_HOURS):
            await prompts.dismiss(context.bot, draft)
        if action == CLOSE:
            await delete_draft(context.bot, db, draft)
            await db.commit()
            await query.edit_message_text("✏️ Editing closed.")
            return
        if action == SAVE:
            await _save_attendance(update, context, db, draft)
            return
        if action == CONFIRM_CANCEL:
            await _cancel_session(update, context, db, draft)
            return
        if action == SET_PAYER:
            await _change_booking(update, context, db, draft, payer_id=int(value))
            return
        if action == SET_HOURS:
            await _change_booking(update, context, db, draft, minutes=int(value))
            return
        if action in (PRICE, OTHER_HOURS):
            question, field = (
                ("what's the correct price for this booking? (e.g. 14.80)", "price")
                if action == PRICE
                else ("how many hours is it booked for? (e.g. 3 or 1.5)", "minutes")
            )
            await prompts.ask(
                context.bot, draft, question, owner=update.effective_user, field=field
            )
            await db.commit()
            return
        if action in (BOOKINGS, PAYER, HOURS, CANCEL_SESSION):
            update_data(draft, screen=action)
        elif action == BOOKING:
            update_data(draft, screen=BOOKING, booking_id=int(value))
        if action == SESSIONS:
            update_data(draft, screen=SESSIONS, session_id=None, ins=None, shown_ins=None)
        elif action == PICK_SESSION:
            update_data(
                draft,
                screen="menu",
                session_id=int(value),
                ins=None,
                shown_ins=None,
                booking_id=None,
            )
        elif action == ATTENDANCE:
            ins = await _current_ins(db, draft.data["session_id"])
            update_data(draft, screen=ATTENDANCE, ins=ins, shown_ins=ins)
        elif action == TOGGLE:
            ins = set(draft.data["ins"]) ^ {int(value)}
            update_data(draft, ins=sorted(ins))
        text, markup = await render(db, draft)
        await db.commit()
        await query.edit_message_text(text, reply_markup=markup)


async def _save_attendance(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft
) -> None:
    session_id = draft.data["session_id"]
    shown, chosen = set(draft.data["shown_ins"]), set(draft.data["ins"])
    # Apply only the admin's toggles: anyone who answered since the screen was opened
    # keeps their answer.
    before = set(await _current_ins(db, session_id))
    after = (before - (shown - chosen)) | (chosen - shown)
    added, removed = after - before, before - after
    session = await db.get(Session, session_id)
    when = fmt_session(session.starts_at, session.ends_at)
    if not added and not removed:
        await delete_draft(context.bot, db, draft)
        await db.commit()
        await update.callback_query.edit_message_text(f"✏️ {when}: no changes.")
        return

    for member_id in added:
        await set_attendance(db, session_id, member_id, IN)
    for member_id in removed:
        await set_attendance(db, session_id, member_id, OUT)
    admin = await upsert_from_telegram(db, update.effective_user)
    names = {m.id: m.display_name for m in await _members(db, before | after)}
    audit.record(
        db,
        actor_member_id=admin.id,
        action="attendance.edit",
        session_id=session_id,
        before=sorted(names[i] for i in before),
        after=sorted(names[i] for i in after),
    )
    await delete_draft(context.bot, db, draft)
    await db.commit()

    changes = " ".join(
        [f"+{html.escape(names[i])}" for i in sorted(added, key=lambda i: names[i].casefold())]
        + [f"−{html.escape(names[i])}" for i in sorted(removed, key=lambda i: names[i].casefold())]
    )
    lines = [f"✏️ Admin changed attendance for <b>{when}</b>: {changes}"]
    lines.append(_split_line(await load_card_view(db, session_id)))
    await update.callback_query.edit_message_text(f"✅ Saved attendance for {when}.")
    await refresh_card(context.bot, db, session_id, locked=session.locked_at is not None)
    await announce(context.bot, settings_of(context), "\n".join(lines))


def register(application: Application) -> None:
    application.add_handler(CommandHandler("edit", start_edit))
    application.add_handler(CallbackQueryHandler(on_button, pattern=rf"^{CALLBACK_KIND}:\d+:"))


def _split_line(view: CardView) -> str:
    """The recalculated court split after a change."""
    if not view.ins:
        return "Nobody is marked in now, so this session is left out of the costs."
    return f"💵 Court now {view.court_each()} each ({len(view.ins)} played)"


async def _show(context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft) -> None:
    text, markup = await render(db, draft)
    await show_draft(context.bot, draft, text, markup)


async def _change_booking(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db: AsyncSession,
    draft: Draft,
    *,
    amount_cents: int | None = None,
    payer_id: int | None = None,
    minutes: int | None = None,
) -> None:
    """Apply one correction to a booking, then announce, audit and refresh."""
    booking = await db.get(Booking, draft.data["booking_id"])
    unchanged = (
        amount_cents in (None, booking.amount_cents)
        and payer_id in (None, booking.payer_member_id)
        and minutes in (None, booking.minutes)
    )
    if unchanged:  # e.g. a second tap on the same payer, which waited for the first
        on_booking = draft.data.get("screen") == BOOKING
        update_data(draft, screen=BOOKING)
        if not on_booking:  # back from the payer or hours picker
            await _show(context, db, draft)
        await db.commit()
        return
    session = await db.get(Session, booking.session_id)
    when = fmt_session(session.starts_at, session.ends_at)
    names = {m.id: m.display_name for m in await db.scalars(select(Member))}
    owner = html.escape(names[booking.payer_member_id])
    before = {
        "amount_cents": booking.amount_cents,
        "payer_id": booking.payer_member_id,
        "minutes": booking.minutes,
    }

    if amount_cents is not None:
        change = f"price {fmt_sgd(booking.amount_cents)} → {fmt_sgd(amount_cents)}"
        booking.amount_cents = amount_cents
    elif payer_id is not None:
        change = (
            f"paid by {html.escape(names[booking.payer_member_id])} → "
            f"{html.escape(names[payer_id])}"
        )
        booking.payer_member_id = payer_id
    else:
        change = f"{fmt_hours(booking.minutes)} → {fmt_hours(minutes)}"
        booking.minutes = minutes
        await db.flush()
        session = await recompute_span(db, session.id)

    admin = await upsert_from_telegram(db, update.effective_user)
    audit.record(
        db,
        actor_member_id=admin.id,
        action="booking.edit",
        booking_id=booking.id,
        before=before,
        after={
            "amount_cents": booking.amount_cents,
            "payer_id": booking.payer_member_id,
            "minutes": booking.minutes,
        },
    )
    update_data(draft, screen=BOOKING)
    await db.commit()

    lines = [f"✏️ Admin changed {owner}'s booking for <b>{when}</b>: {change}"]
    if minutes is not None:
        lines.append(f"📅 The session is now {fmt_session(session.starts_at, session.ends_at)}")
    lines.append(_split_line(await load_card_view(db, session.id)))
    await _show(context, db, draft)
    await refresh_record(context.bot, db, booking, "✏️ Corrected by the admin.")
    await refresh_card(context.bot, db, session.id, locked=session.locked_at is not None)
    await announce(context.bot, settings_of(context), "\n".join(lines))


async def _cancel_session(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft
) -> None:
    now = utc_now()
    session = await cancel_session(db, draft.data["session_id"], now)
    when = fmt_session(session.starts_at, session.ends_at)
    admin = await upsert_from_telegram(db, update.effective_user)
    audit.record(
        db,
        actor_member_id=admin.id,
        action="session.cancel",
        session_id=session.id,
        before={"status": "scheduled"},
        after={"status": "cancelled"},
    )
    await delete_draft(context.bot, db, draft)
    await db.commit()
    await update.callback_query.edit_message_text(f"🗑 Cancelled the session on {when}.")
    note = f"Cancelled by {html.escape(admin.display_name)} with the session."
    for booking in session.bookings:
        if booking.cancelled_at == now:  # not the ones cancelled before
            await refresh_record(context.bot, db, booking, note)
    await close_cancelled_card(context.bot, session)
    await announce(
        context.bot,
        settings_of(context),
        f"🗑 Admin cancelled the session on <b>{when}</b>. Nobody is charged for it.",
    )


@prompts.on_reply(KIND)
async def on_typed_answer(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft, text: str
) -> None:
    try:
        if draft.data.get("awaiting") == "price":
            changes = {"amount_cents": parse_amount(text)}
        else:
            changes = {"minutes": parse_hours(text)}
    except ValueError as error:
        raise prompts.PromptError(str(error)) from None
    await _change_booking(update, context, db, draft, **changes)
