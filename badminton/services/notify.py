"""Sending to the group, to members' DMs and to the admin."""

from __future__ import annotations

import logging
from typing import Any

from telegram import Bot, Message
from telegram.error import BadRequest, Forbidden

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
    DMs off (FR-MEM-3) and carry on; the caller commits that change."""
    if not member.dm_enabled or member.telegram_user_id is None:
        return False
    try:
        await bot.send_message(member.telegram_user_id, text, **kwargs)
    except (Forbidden, BadRequest) as error:
        logger.info("Turning off DMs for member %s: %s", member.id, error)
        member.dm_enabled = False
        return False
    return True


async def dm_admins(bot: Bot, settings: Settings, text: str, **kwargs: Any) -> None:
    """Tell every admin privately. An admin who hasn't /start-ed the bot is skipped."""
    for admin_id in sorted(settings.admin_ids):
        try:
            await bot.send_message(admin_id, text, **kwargs)
        except (Forbidden, BadRequest) as error:
            logger.warning("Could not DM admin %s: %s", admin_id, error)
