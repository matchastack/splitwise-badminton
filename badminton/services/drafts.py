"""Wizard state (FR-BK-9). Functions are stateless, so unfinished wizards live in the
database and expire after a day."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot
from telegram.error import BadRequest

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


async def get_draft(db: AsyncSession, draft_id: int, *, now: datetime) -> Draft | None:
    """The draft, unless it doesn't exist or has expired."""
    draft = await db.get(Draft, draft_id)
    return draft if draft is not None and draft.expires_at > now else None


async def find_by_prompt(
    db: AsyncSession, *, chat_id: int, prompt_message_id: int, now: datetime
) -> Draft | None:
    return await db.scalar(
        select(Draft).where(
            Draft.chat_id == chat_id,
            Draft.prompt_message_id == prompt_message_id,
            Draft.expires_at > now,
        )
    )


def update_data(draft: Draft, **changes: Any) -> None:
    """Merge ``changes`` into the draft's data. Assigning a new dict marks the JSONB
    column as changed; mutating it in place would not."""
    data = {**draft.data, **changes}
    for key, value in changes.items():
        if value is None:
            data.pop(key)
    draft.data = data


async def delete_draft(db: AsyncSession, draft: Draft) -> None:
    await db.delete(draft)


def is_deleted(db: AsyncSession, draft: Draft) -> bool:
    """Whether a reply handler finished the wizard by deleting its draft."""
    return draft in db.deleted or inspect(draft).was_deleted


async def delete_message_quietly(bot: Bot, chat_id: int, message_id: int | None) -> None:
    """Delete a message, ignoring ones that are already gone or too old to delete."""
    if message_id is None:
        return
    try:
        await bot.delete_message(chat_id, message_id)
    except BadRequest as error:
        logger.info("Could not delete message %s in %s: %s", message_id, chat_id, error)


async def cleanup_expired_drafts(context: TickContext) -> None:
    """Tick step: delete expired wizards and their messages (FR-BK-9)."""
    async with context.sessionmaker() as db:
        expired = (await db.scalars(select(Draft).where(Draft.expires_at <= context.now))).all()
        for draft in expired:
            await delete_message_quietly(context.bot, draft.chat_id, draft.message_id)
            await delete_message_quietly(context.bot, draft.chat_id, draft.prompt_message_id)
            await delete_draft(db, draft)
        await db.commit()
