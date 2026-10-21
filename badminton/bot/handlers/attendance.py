"""[✅ I'm in] / [❌ I'm out] on the attendance card (FR-ATT-3, FR-ATT-5)."""

from __future__ import annotations

import contextlib

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from badminton.bot.context import db_session
from badminton.db.models import Session
from badminton.domain.timeutil import utc_now
from badminton.services.attendance import IN, load_card_view, render_card, set_attendance
from badminton.services.members import upsert_from_telegram

LOCKED = "Attendance is locked: this session has ended."


async def on_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, status, session_id = query.data.split(":")
    async with db_session(context) as db:
        session = await db.get(Session, int(session_id))
        if session is None or session.status == "cancelled":
            await query.answer("This session was cancelled.", show_alert=True)
            return
        if utc_now() >= session.ends_at:
            await query.answer(LOCKED, show_alert=True)
            return
        member = await upsert_from_telegram(db, update.effective_user)
        await set_attendance(db, session.id, member.id, status)
        await db.commit()
        text, markup = render_card(await load_card_view(db, session.id))

    await query.answer("You're in ✅" if status == IN else "You're out")
    with contextlib.suppress(BadRequest):  # "message is not modified" on a repeated tap
        await query.edit_message_text(text, reply_markup=markup)


def register(application: Application) -> None:
    application.add_handler(CallbackQueryHandler(on_answer, pattern=r"^att:(in|out):\d+$"))
