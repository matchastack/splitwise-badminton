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
    group_chat_id = settings_of(context).group_chat_id
    if group_chat_id is None:
        # Not set up yet: only /chatid is useful, so let it through anywhere.
        message = update.effective_message
        if message is not None and (message.text or "").startswith("/chatid"):
            return
    elif chat.id == group_chat_id:
        return
    raise ApplicationHandlerStop


def register(application: Application) -> None:
    application.add_handler(TypeHandler(Update, drop_foreign_chats), group=GATE_GROUP)
