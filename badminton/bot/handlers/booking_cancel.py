"""Cancelling a booking from its record (FR-BK-10).

The submitter or the admin can cancel before the session starts. A second tap
confirms, to avoid accidents. The cancellation is announced and audited.
"""

from __future__ import annotations

import contextlib
import html
from datetime import timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from badminton.bot.context import db_session, settings_of
from badminton.bot.handlers.booking import cancel_markup, render_record
from badminton.db.models import Booking, Member, Session
from badminton.domain.money import fmt_sgd
from badminton.domain.timeutil import fmt_session, utc_now
from badminton.services import audit
from badminton.services.attendance import refresh_card
from badminton.services.members import mention_html, upsert_from_telegram
from badminton.services.notify import announce
from badminton.services.sessions import cancel_booking

ASK = "bkc"
YES = "bkcy"
KEEP = "bkck"


async def on_cancel_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action, booking_id = query.data.split(":")
    async with db_session(context) as db:
        booking = await db.get(Booking, int(booking_id))
        if booking is None or booking.cancelled_at is not None:
            await query.answer("This booking is already cancelled.", show_alert=True)
            return
        submitter = await db.get(Member, booking.submitted_by_member_id)
        user_id = update.effective_user.id
        if user_id != submitter.telegram_user_id and not settings_of(context).is_admin(user_id):
            await query.answer(
                f"Only {submitter.display_name} or the admin can cancel this booking.",
                show_alert=True,
            )
            return
        now = utc_now()
        if booking.starts_at <= now:
            await query.answer("This booking has already started.", show_alert=True)
            return

        if action == ASK:
            await query.answer()
            await query.edit_message_reply_markup(
                InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "Yes, cancel it", callback_data=f"{YES}:{booking.id}"
                            ),
                            InlineKeyboardButton("Keep it", callback_data=f"{KEEP}:{booking.id}"),
                        ]
                    ]
                )
            )
            return
        if action == KEEP:
            await query.answer()
            await query.edit_message_reply_markup(cancel_markup(booking.id))
            return

        actor = await upsert_from_telegram(db, update.effective_user)
        payer = await db.get(Member, booking.payer_member_id)
        session = await cancel_booking(db, booking, now)
        audit.record(
            db,
            actor_member_id=actor.id,
            action="booking.cancel",
            booking_id=booking.id,
            session_id=session.id,
            before={"cancelled_at": None},
            after={"cancelled_at": now},
        )
        await db.commit()

        record = render_record(payer, booking.starts_at, booking.minutes, booking.amount_cents)
        await query.answer("Booking cancelled.")
        await query.edit_message_text(
            f"🗑 <s>{record}</s>\nCancelled by {html.escape(actor.display_name)}."
        )
        await _close_card_if_cancelled(context, session)
        await refresh_card(context.bot, db, session.id)
        what = fmt_session(
            booking.starts_at, booking.starts_at + timedelta(minutes=booking.minutes)
        )
        lines = [
            f"🗑 {html.escape(actor.display_name)} cancelled {mention_html(payer)}'s booking "
            f"for {what} ({fmt_sgd(booking.amount_cents)})."
        ]
        if session.status == "cancelled":
            lines.append("That session is now cancelled.")
        await announce(context.bot, settings_of(context), "\n".join(lines))


async def _close_card_if_cancelled(context: ContextTypes.DEFAULT_TYPE, session: Session) -> None:
    """Close the session's attendance card if the session no longer has any bookings."""
    if session.status != "cancelled" or session.card_message_id is None:
        return
    with contextlib.suppress(BadRequest):  # the card was deleted or can't be edited
        await context.bot.edit_message_text(
            f"❌ <b>Session cancelled</b>: {fmt_session(session.starts_at, session.ends_at)}",
            chat_id=session.card_chat_id,
            message_id=session.card_message_id,
        )


def register(application: Application) -> None:
    application.add_handler(
        CallbackQueryHandler(on_cancel_button, pattern=rf"^({ASK}|{YES}|{KEEP}):\d+$")
    )
