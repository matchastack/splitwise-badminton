"""Admin member management: /addmember and /members (FR-MEM-5).

Guests (people without Telegram) are added by name so they can be marked in and
charged. Every change is announced in the group and audited (FR-ADM-3, FR-ADM-5).
"""

from __future__ import annotations

import html

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from badminton.bot import prompts
from badminton.bot.admin import admin_only
from badminton.bot.context import db_session, settings_of
from badminton.db.models import Draft, Member
from badminton.domain.timeutil import utc_now
from badminton.services import audit
from badminton.services.drafts import create_draft, delete_draft
from badminton.services.members import (
    all_members,
    member_label,
    upsert_from_telegram,
    validate_name,
)
from badminton.services.notify import announce

RENAME_KIND = "member_rename"


def render_members(members: list[Member]) -> tuple[str, InlineKeyboardMarkup]:
    lines = ["👥 <b>Members</b>"]
    buttons = []
    for member in members:
        lines.append(f"• {html.escape(member_label(member))}")
        toggle = "↩️ Reactivate" if not member.active else "🚫 Deactivate"
        buttons.append(
            [
                InlineKeyboardButton(
                    f"✏️ {member.display_name}", callback_data=f"mem:ren:{member.id}"
                ),
                InlineKeyboardButton(toggle, callback_data=f"mem:act:{member.id}"),
            ]
        )
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


@admin_only
async def add_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        name = validate_name(" ".join(context.args or ()))
    except ValueError:
        await update.effective_message.reply_text("Usage: /addmember &lt;name&gt;")
        return
    async with db_session(context) as db:
        admin = await upsert_from_telegram(db, update.effective_user)
        member = Member(display_name=name)
        db.add(member)
        await db.flush()
        audit.record(
            db, actor_member_id=admin.id, action="member.add", after={"id": member.id, "name": name}
        )
        await db.commit()
    await announce(
        context.bot,
        settings_of(context),
        f"➕ Admin added guest member <b>{html.escape(name)}</b>.",
    )


@admin_only
async def list_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with db_session(context) as db:
        text, markup = render_members(await all_members(db))
    await update.effective_message.reply_text(text, reply_markup=markup)


@admin_only
async def toggle_active(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    member_id = int(query.data.rsplit(":", 1)[1])
    async with db_session(context) as db:
        member = await db.get(Member, member_id)
        if member is None:
            await query.answer("That member no longer exists.", show_alert=True)
            return
        admin = await upsert_from_telegram(db, update.effective_user)
        member.active = not member.active
        audit.record(
            db,
            actor_member_id=admin.id,
            action="member.activate" if member.active else "member.deactivate",
            member_id=member.id,
        )
        name = html.escape(member.display_name)
        active = member.active
        await db.commit()
        text, markup = render_members(await all_members(db))
    await query.answer()
    await query.edit_message_text(text, reply_markup=markup)
    verb = "↩️ Admin reactivated" if active else "🚫 Admin deactivated"
    await announce(context.bot, settings_of(context), f"{verb} <b>{name}</b>.")


@admin_only
async def start_rename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    member_id = int(query.data.rsplit(":", 1)[1])
    async with db_session(context) as db:
        member = await db.get(Member, member_id)
        if member is None:
            await query.answer("That member no longer exists.", show_alert=True)
            return
        draft = await create_draft(
            db,
            kind=RENAME_KIND,
            chat_id=query.message.chat_id,
            user_id=update.effective_user.id,
            now=utc_now(),
            # Not the draft's message_id: that one is deleted when the draft expires.
            data={"member_id": member.id, "list_message_id": query.message.message_id},
        )
        await prompts.ask(
            context.bot,
            draft,
            f"what should <b>{html.escape(member.display_name)}</b> be called?",
            owner=update.effective_user,
            field="name",
        )
        await db.commit()
    await query.answer()


@prompts.on_reply(RENAME_KIND)
async def finish_rename(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft, text: str
) -> None:
    try:
        name = validate_name(text)
    except ValueError as error:
        raise prompts.PromptError(
            str(error).replace("Usage: /name <your name>", "Enter a name.")
        ) from None
    member = await db.get(Member, draft.data["member_id"])
    admin = await upsert_from_telegram(db, update.effective_user)
    old_name = member.display_name
    member.display_name = name
    audit.record(
        db,
        actor_member_id=admin.id,
        action="member.rename",
        before=old_name,
        after=name,
        member_id=member.id,
    )
    await db.flush()
    text_, markup = render_members(await all_members(db))
    await context.bot.edit_message_text(
        text_, chat_id=draft.chat_id, message_id=draft.data["list_message_id"], reply_markup=markup
    )
    await delete_draft(context.bot, db, draft)
    await announce(
        context.bot,
        settings_of(context),
        f"✏️ Admin renamed <b>{html.escape(old_name)}</b> to <b>{html.escape(name)}</b>.",
    )


def register(application: Application) -> None:
    application.add_handler(CommandHandler("addmember", add_member))
    application.add_handler(CommandHandler("members", list_members))
    application.add_handler(CallbackQueryHandler(toggle_active, pattern=r"^mem:act:\d+$"))
    application.add_handler(CallbackQueryHandler(start_rename, pattern=r"^mem:ren:\d+$"))
