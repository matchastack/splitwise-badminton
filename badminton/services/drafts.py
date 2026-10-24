"""Wizard state (FR-BK-9). Functions are stateless, so unfinished wizards live in the
database and expire after a day."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden

from badminton.db.models import Draft
from badminton.services.tick import TickContext

DRAFT_TTL = timedelta(hours=24)

logger = logging.getLogger(__name__)


async def create_draft(
    db: AsyncSession,
    *,
    kind: str,
    chat_id: int,
    user_id: int,
    now: datetime,
    data: dict[str, Any] | None = None,
    message_id: int | None = None,
) -> Draft:
    draft = Draft(
        kind=kind,
        chat_id=chat_id,
        user_id=user_id,
        message_id=message_id,
        data=data or {},
        expires_at=now + DRAFT_TTL,
    )
    db.add(draft)
    await db.flush()
    return draft


async def get_draft(
    db: AsyncSession, draft_id: int, *, now: datetime, lock: bool = False
) -> Draft | None:
    """The draft, unless it doesn't exist or has expired. With ``lock``, the row stays
    locked until the commit: Telegram may deliver a double-tap as two parallel requests, so
    the second waits, then finds the draft gone if the first finished the wizard."""
    draft = await db.get(Draft, draft_id, with_for_update=lock)
    return draft if draft is not None and draft.expires_at > now else None


async def find_by_prompt(
    db: AsyncSession, *, chat_id: int, prompt_message_id: int, now: datetime, lock: bool = False
) -> Draft | None:
    """The draft whose open prompt this is. ``lock`` as for ``get_draft``: an answer that
    waited finds no draft once the prompt it replies to is closed."""
    query = select(Draft).where(
        Draft.chat_id == chat_id,
        Draft.prompt_message_id == prompt_message_id,
        Draft.expires_at > now,
    )
    return await db.scalar(query.with_for_update() if lock else query)


def update_data(draft: Draft, **changes: Any) -> None:
    """Merge ``changes`` into the draft's data; a ``None`` value removes the key.
    Assigning a new dict marks the JSONB column as changed; mutating it would not."""
    data = {**draft.data, **changes}
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
    draft.data = data


async def delete_draft(bot: Bot, db: AsyncSession, draft: Draft) -> None:
    """Delete the draft, its open reply prompt, if any, and the messages it owns
    (``data["owned_message_ids"]``, e.g. the screenshot sent to /court): with the wizard
    gone, nothing could handle an answer, and nothing needs them."""
    await delete_message_quietly(bot, draft.chat_id, draft.prompt_message_id)
    for message_id in draft.data.get("owned_message_ids", ()):
        await delete_message_quietly(bot, draft.chat_id, message_id)
    draft.prompt_message_id = None
    await db.delete(draft)


async def show_draft(bot: Bot, draft: Draft, text: str, markup: InlineKeyboardMarkup) -> None:
    """Edit the wizard's card to show ``text`` and ``markup``. Telegram refuses an edit
    that changes nothing ("message is not modified"), e.g. after a double tap; the card
    already shows it, so that is ignored. Any other error is raised."""
    try:
        await bot.edit_message_text(
            text, chat_id=draft.chat_id, message_id=draft.message_id, reply_markup=markup
        )
    except BadRequest as error:
        if "message is not modified" not in error.message.lower():
            raise


def is_deleted(db: AsyncSession, draft: Draft) -> bool:
    """Whether a reply handler finished the wizard by deleting its draft."""
    return draft in db.deleted or inspect(draft).was_deleted


async def delete_message_quietly(bot: Bot, chat_id: int, message_id: int | None) -> None:
    """Delete a message, ignoring ones that are already gone, too old to delete, or in a
    chat the bot can no longer reach."""
    if message_id is None:
        return
    try:
        await bot.delete_message(chat_id, message_id)
    except (BadRequest, Forbidden) as error:
        logger.info("Could not delete message %s in %s: %s", message_id, chat_id, error)


async def cleanup_expired_drafts(context: TickContext) -> None:
    """Tick step: delete expired wizards and their messages (FR-BK-9)."""
    async with context.sessionmaker() as db:
        expired = (await db.scalars(select(Draft).where(Draft.expires_at <= context.now))).all()
        for draft in expired:
            await delete_message_quietly(context.bot, draft.chat_id, draft.message_id)
            await delete_draft(context.bot, db, draft)
        await db.commit()
