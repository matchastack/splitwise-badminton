"""Sending to the group, to members' DMs and to the admin."""

from __future__ import annotations

import logging
from typing import Any

from telegram import Bot, Message
from telegram.error import BadRequest, Forbidden, TelegramError

from badminton.config import Settings
from badminton.db.models import Member

logger = logging.getLogger(__name__)


async def announce(bot: Bot, settings: Settings, text: str, **kwargs: Any) -> Message | None:
    """Post to the group. Admin changes are always announced here (FR-ADM-3, FR-ADM-6)."""
    if settings.group_chat_id is None:
        logger.warning("GROUP_CHAT_ID is not set; not announcing: %s", text)
        return None
    return await bot.send_message(settings.group_chat_id, text, **kwargs)


async def dm(bot: Bot, member: Member, text: str, **kwargs: Any) -> bool:
    """DM a member who has /start-ed the bot. If they have since blocked it, turn their
    DMs off (FR-MEM-3) and carry on; the caller commits that change. Any other rejected
    message is skipped, with DMs left on."""
    if not member.dm_enabled or member.telegram_user_id is None:
        return False
    try:
        await bot.send_message(member.telegram_user_id, text, **kwargs)
    except Forbidden as error:
        logger.info("Turning off DMs for member %s: %s", member.id, error)
        member.dm_enabled = False
        return False
    except BadRequest as error:
        logger.warning("Could not DM member %s: %s", member.id, error)
        return False
    return True


async def pin(bot: Bot, chat_id: int, message_id: int) -> None:
    """Pin a message without notifying anyone (FR-ATT-4: no pings). The bot needs the
    "Pin messages" admin right. Pins are a convenience: if one fails, even on a network
    error, the message stays unpinned and the caller carries on."""
    try:
        await bot.pin_chat_message(chat_id, message_id, disable_notification=True)
    except TelegramError as error:
        logger.warning(
            "Could not pin message %s: %s (can the bot pin messages?)", message_id, error
        )


async def unpin(bot: Bot, chat_id: int | None, message_id: int | None) -> None:
    """Unpin a message, if it is still there and pinned. As with ``pin``, a failure is
    logged and the caller carries on."""
    if chat_id is None or message_id is None:
        return
    try:
        await bot.unpin_chat_message(chat_id, message_id)
    except TelegramError as error:
        logger.info("Could not unpin message %s: %s", message_id, error)


async def dm_admins(bot: Bot, settings: Settings, text: str, **kwargs: Any) -> None:
    """Tell every admin privately. An admin who hasn't /start-ed the bot is skipped."""
    for admin_id in sorted(settings.admin_ids):
        try:
            await bot.send_message(admin_id, text, **kwargs)
        except (Forbidden, BadRequest) as error:
            logger.warning("Could not DM admin %s: %s", admin_id, error)
