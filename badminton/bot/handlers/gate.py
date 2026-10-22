"""Ignore updates from any chat other than the configured group and private chats (FR-MOD-4)."""

from __future__ import annotations

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import Application, ApplicationHandlerStop, ContextTypes, TypeHandler

from badminton.bot.context import settings_of

GATE_GROUP = -100  # runs before every other handler group


async def drop_foreign_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None or chat.type == ChatType.PRIVATE:
        return
    settings = settings_of(context)
    if chat.id == settings.group_chat_id:
        return
    message = update.effective_message
    if message is not None and (message.text or "").startswith("/chatid"):
        # Only /chatid is useful elsewhere: anyone's before setup, and the admin's after,
        # e.g. once the group became a supergroup and got a new ID.
        user = update.effective_user
        if settings.group_chat_id is None or (user is not None and settings.is_admin(user.id)):
            return
    raise ApplicationHandlerStop


def register(application: Application) -> None:
    application.add_handler(TypeHandler(Update, drop_foreign_chats), group=GATE_GROUP)
