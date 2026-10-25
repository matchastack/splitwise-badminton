"""Member commands: /start, /name, /help and /chatid (FR-MEM-2, 4, 7, 9, FR-OPS-5).

All but /chatid work in a private chat only, for the group's members (FR-MEM-9).
"""

from __future__ import annotations

import html

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from badminton.bot.commands import ADMIN_COMMANDS, BOOKING_COMMAND, MEMBER_COMMANDS
from badminton.bot.context import db_session, settings_of
from badminton.bot.membership import members_only, private_only
from badminton.services.members import upsert_from_telegram, validate_name


@members_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with db_session(context) as db:
        member = await upsert_from_telegram(db, update.effective_user)
        member.dm_enabled = True
        name = member.display_name
        await db.commit()
    await update.effective_message.reply_text(
        f"👋 Hi <b>{html.escape(name)}</b>! I'll send your monthly statement here.\n\n"
        "Send me your commands here, and I'll post what you record in the group. For a "
        f"court booking, send /{BOOKING_COMMAND} and then your booking screenshot. "
        "Type /help to see everything I can do."
    )


@private_only
@members_only
async def set_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not context.args:
        await message.reply_text("Usage: /name &lt;your name&gt;")
        return
    try:
        name = validate_name(" ".join(context.args))
    except ValueError as error:
        await message.reply_text(html.escape(str(error)))
        return
    async with db_session(context) as db:
        member = await upsert_from_telegram(db, update.effective_user)
        member.display_name = name
        await db.commit()
    await message.reply_text(f"✅ I'll call you <b>{html.escape(name)}</b>.")


@private_only
@members_only
async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = ["🏸 <b>Badminton bot</b>", ""]
    lines += [f"/{command.command} — {command.description}" for command in MEMBER_COMMANDS]
    if settings_of(context).is_admin(update.effective_user.id):
        lines += ["", "<b>Admin</b>"]
        lines += [
            f"/{command.command} — {command.description}"
            for command in ADMIN_COMMANDS
            if command not in MEMBER_COMMANDS
        ]
    lines += ["", "Send these to me here; I post what you record in the group."]
    await update.effective_message.reply_text("\n".join(lines))


async def chat_id(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(f"Chat ID: <code>{update.effective_chat.id}</code>")


def register(application: Application) -> None:
    application.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("name", set_name))
    application.add_handler(CommandHandler("help", show_help))
    application.add_handler(CommandHandler("chatid", chat_id))
