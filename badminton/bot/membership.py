"""Commands are sent to the bot in a private chat, and only its group's members may
(FR-MEM-9). The group gets the results: booking records, announcements and cards.

Anyone can message a public bot, so before a command in a private chat runs, the bot
asks Telegram whether the sender is in the group. Telegram only guarantees that answer
to a bot that is a group admin; this one is, to pin bookings.
"""

from __future__ import annotations

import functools
import html
import logging

from telegram import Bot, ChatMemberRestricted, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import BadRequest, Forbidden
from telegram.ext import ContextTypes

from badminton.bot.admin import Handler
from badminton.bot.context import settings_of
from badminton.config import Settings

NOT_A_MEMBER = "🏸 Sorry, I only work for members of the badminton group."
PRIVATELY = "🔒 Send {command} to me in a private chat."
OPEN_CHAT = "💬 Open my chat"

IN_GROUP = {ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER}

logger = logging.getLogger(__name__)


async def is_group_member(bot: Bot, settings: Settings, user_id: int) -> bool:
    """Whether the user is the admin or in the group, as owner, administrator, member, or
    restricted but still a member. A lookup that fails (the user isn't found, or the bot
    is no longer in the group) counts as not a member."""
    if settings.is_admin(user_id):
        return True
    if settings.group_chat_id is None:
        return False
    try:
        member = await bot.get_chat_member(settings.group_chat_id, user_id)
    except (BadRequest, Forbidden) as error:
        logger.warning("Could not check whether user %s is in the group: %s", user_id, error)
        return False
    if isinstance(member, ChatMemberRestricted):
        return member.is_member
    return member.status in IN_GROUP


def members_only(handler: Handler) -> Handler:
    """In a private chat, run ``handler`` only for the admin or a member of the group, and
    refuse anyone else before it makes them a member record. In the group, just run it:
    whoever sends a command there is in it."""

    @functools.wraps(handler)
    async def guarded(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await _may_use(update, context):
            await handler(update, context)
        elif update.callback_query is not None:
            await update.callback_query.answer(NOT_A_MEMBER, show_alert=True)
        elif update.effective_message is not None:
            await update.effective_message.reply_text(NOT_A_MEMBER)

    return guarded


async def _may_use(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat, user = update.effective_chat, update.effective_user
    if chat is None or chat.type != ChatType.PRIVATE:
        return True
    return user is not None and await is_group_member(context.bot, settings_of(context), user.id)


def private_only(handler: Handler) -> Handler:
    """Run ``handler`` only in a private chat. In the group, say to send the command to the
    bot privately, with a button that opens that chat."""

    @functools.wraps(handler)
    async def guarded(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat.type == ChatType.PRIVATE:
            await handler(update, context)
        else:
            await send_privately(update, context)

    return guarded


async def send_privately(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to a command sent in the group, as text or as a caption: it goes to the bot."""
    message = update.effective_message
    command = (message.text or message.caption).split(maxsplit=1)[0].partition("@")[0].lower()
    await message.reply_text(
        PRIVATELY.format(command=html.escape(command)),
        reply_markup=open_chat_markup(context.bot.username),
    )


def open_chat_button(bot_username: str) -> InlineKeyboardButton:
    """A button that opens the private chat with the bot."""
    return InlineKeyboardButton(OPEN_CHAT, url=f"https://t.me/{bot_username}")


def open_chat_markup(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[open_chat_button(bot_username)]])
