"""Keep the group commands-only (FR-MOD-1, FR-MOD-3).

Registered last, so it only sees messages no other handler took: plain chat,
stickers, media, unknown commands, and replies that don't answer one's own prompt.
The bot must be a group admin with "Delete messages" for this to work.
"""

from __future__ import annotations

import logging

from telegram import Message, Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from badminton.bot.context import settings_of

logger = logging.getLogger(__name__)

# A /court photo is always allowed: it is the booking's proof (FR-BK-1).
COURT_PHOTO = filters.PHOTO & filters.CaptionRegex(r"^/court(@\w+)?(\s|$)")


def is_exempt(message: Message, context: ContextTypes.DEFAULT_TYPE) -> bool:
    settings = settings_of(context)
    if message.chat_id != settings.group_chat_id:
        return True  # private chats are never moderated
    if message.sender_chat is not None and message.sender_chat.id == message.chat_id:
        return True  # an anonymous group admin
    if message.from_user is not None and settings.is_admin(message.from_user.id):
        return True
    return bool(filters.StatusUpdate.ALL.check_update(_as_update(message))) or bool(
        COURT_PHOTO.check_update(_as_update(message))
    )


async def delete_unless_exempt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or is_exempt(message, context):
        return
    try:
        await message.delete()
    except (BadRequest, Forbidden) as error:
        logger.warning(
            "Could not delete message %s: %s (is the bot a group admin?)", message.id, error
        )


def _as_update(message: Message) -> Update:
    return Update(update_id=0, message=message)


def register(application: Application) -> None:
    application.add_handler(MessageHandler(filters.ChatType.GROUPS, delete_unless_exempt))
