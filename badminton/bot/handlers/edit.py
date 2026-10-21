"""/edit: the admin corrects a session (FR-ADM-2, FR-ADM-3, FR-ADM-5).

Flow: recent-session picker → session menu → attendance toggles → Save. Every
saved change is announced in the group with the recalculated split, audited, and
the session's attendance card is refreshed.
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
from badminton.db.models import Draft, Member, Session
from badminton.domain.money import allocate, fmt_sgd
from badminton.domain.timeutil import fmt_date, fmt_session, fmt_time, sgt_date, utc_now
from badminton.services import audit
from badminton.services.attendance import IN, OUT, load_card_view, refresh_card, set_attendance
from badminton.services.drafts import create_draft, delete_draft, get_draft, update_data
from badminton.services.members import active_members, upsert_from_telegram
from badminton.services.notify import announce

KIND = "edit"
CALLBACK_KIND = "ed"
RECENT_SESSIONS = 8

PICK_SESSION = "s"
SESSIONS = "list"
ATTENDANCE = "att"
TOGGLE = "t"
SAVE = "save"
CLOSE = "x"


def _button(text: str, draft: Draft, action: str, value: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=encode(CALLBACK_KIND, draft.id, action, value))


def _label(session: Session) -> str:
    when = f"{fmt_date(sgt_date(session.starts_at))} {fmt_time(session.starts_at)}"
    return f"{when} 🔒" if session.locked_at else when


async def render(db: AsyncSession, draft: Draft) -> tuple[str, InlineKeyboardMarkup]:
    data = draft.data
    screen = data.get("screen", SESSIONS)
    if screen == SESSIONS:
        sessions = await db.scalars(
            select(Session)
            .where(Session.status == "scheduled")
            .order_by(Session.starts_at.desc())
            .limit(RECENT_SESSIONS)
        )
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

    rows = [
        [_button("👥 Attendance", draft, ATTENDANCE)],
        [_button("‹ Sessions", draft, SESSIONS), _button("✖ Close", draft, CLOSE)],
    ]
    return f"{title}\nWhat do you want to change?", InlineKeyboardMarkup(rows)


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
        draft = await get_draft(db, callback.draft_id, now=utc_now())
        if draft is None:
            await query.answer("This form has expired. Use /edit again.", show_alert=True)
            return
        await query.answer()
        action, value = callback.action, callback.value
        if action == CLOSE:
            await delete_draft(db, draft)
            await db.commit()
            await query.edit_message_text("✏️ Editing closed.")
            return
        if action == SAVE:
            await _save_attendance(update, context, db, draft)
            return
        if action == SESSIONS:
            update_data(draft, screen=SESSIONS, session_id=None, ins=None)
        elif action == PICK_SESSION:
            update_data(draft, screen="menu", session_id=int(value), ins=None)
        elif action == ATTENDANCE:
            ins = await _current_ins(db, draft.data["session_id"])
            update_data(draft, screen=ATTENDANCE, ins=ins)
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
    before = set(await _current_ins(db, session_id))
    after = set(draft.data["ins"])
    added, removed = after - before, before - after
    session = await db.get(Session, session_id)
    when = fmt_session(session.starts_at, session.ends_at)
    if not added and not removed:
        await delete_draft(db, draft)
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
    await delete_draft(db, draft)
    await db.commit()

    changes = " ".join(
        [f"+{html.escape(names[i])}" for i in sorted(added, key=lambda i: names[i].casefold())]
        + [f"−{html.escape(names[i])}" for i in sorted(removed, key=lambda i: names[i].casefold())]
    )
    lines = [f"✏️ Admin changed attendance for <b>{when}</b>: {changes}"]
    view = await load_card_view(db, session_id)
    court = sum(cents for _, cents in view.payments)
    if view.ins and court:
        shares = allocate(court, dict.fromkeys(range(len(view.ins)), 1)).values()
        each = fmt_sgd(min(shares))
        if max(shares) != min(shares):
            each = f"{fmt_sgd(min(shares))}–{fmt_sgd(max(shares))}"
        lines.append(f"💵 Court now {each} each ({len(view.ins)} played)")
    elif not view.ins:
        lines.append("Nobody is marked in now, so this session is left out of the costs.")
    await update.callback_query.edit_message_text(f"✅ Saved attendance for {when}.")
    await refresh_card(context.bot, db, session_id, locked=session.locked_at is not None)
    await announce(context.bot, settings_of(context), "\n".join(lines))


def register(application: Application) -> None:
    application.add_handler(CommandHandler("edit", start_edit))
    application.add_handler(CallbackQueryHandler(on_button, pattern=rf"^{CALLBACK_KIND}:\d+:"))
