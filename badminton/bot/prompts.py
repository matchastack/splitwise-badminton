"""Typed answers to wizard questions (FR-BK-5, FR-BK-6, FR-MOD-2).

``ask`` sends a ForceReply prompt tied to a draft. When the draft's owner replies,
the handler registered for the draft's kind gets the text. Afterwards the reply and
the prompt are both deleted, so the group only keeps the bot's cards. A handler
raises ``PromptError`` to ask again with an explanation.
"""

from __future__ import annotations

import html
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot, ForceReply, Update, User
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from badminton.bot.context import db_session
from badminton.bot.handlers import guard
from badminton.db.models import Draft
from badminton.domain.timeutil import utc_now
from badminton.services.drafts import (
    delete_message_quietly,
    find_by_prompt,
    is_deleted,
    update_data,
)

ReplyHandler = Callable[
    [Update, ContextTypes.DEFAULT_TYPE, AsyncSession, Draft, str], Awaitable[None]
]

_reply_handlers: dict[str, ReplyHandler] = {}


class PromptError(ValueError):
    """The answer was invalid; the message is shown when asking again."""


def on_reply(kind: str) -> Callable[[ReplyHandler], ReplyHandler]:
    """Register the handler for typed answers to drafts of ``kind``."""

    def register(handler: ReplyHandler) -> ReplyHandler:
        _reply_handlers[kind] = handler
        return handler

    return register


async def ask(
    bot: Bot,
    draft: Draft,
    question: str,
    *,
    owner: User,
    field: str,
    error: str | None = None,
) -> None:
    """Ask the draft's owner to type an answer for ``field``. Replaces any open prompt. In
    the group the question names the owner; in their private chat, it needn't."""
    await delete_message_quietly(bot, draft.chat_id, draft.prompt_message_id)
    warning = f"⚠️ {html.escape(error)}\n" if error else ""
    if draft.chat_id == owner.id:  # a private chat's ID is its user's
        asked = question[:1].upper() + question[1:]
    else:
        asked = f"{owner.mention_html()}, {question}"
    prompt = await bot.send_message(
        draft.chat_id, f"{warning}{asked}", reply_markup=ForceReply(selective=True)
    )
    draft.prompt_message_id = prompt.message_id
    update_data(draft, awaiting=field, question=question)


async def dismiss(bot: Bot, draft: Draft) -> None:
    """Withdraw the open prompt, if any. Call it when the owner moves on with a button:
    an answer typed afterwards would land wherever the wizard is by then."""
    if draft.prompt_message_id is None:
        return
    await delete_message_quietly(bot, draft.chat_id, draft.prompt_message_id)
    draft.prompt_message_id = None
    update_data(draft, awaiting=None, question=None)


async def handle_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    async with db_session(context) as db:
        draft = await find_by_prompt(
            db,
            chat_id=message.chat_id,
            prompt_message_id=message.reply_to_message.message_id,
            now=utc_now(),
            lock=True,
        )
        if draft is None or draft.user_id != update.effective_user.id:
            await guard.delete_unless_exempt(update, context)
            return
        handler = _reply_handlers[draft.kind]
        await delete_message_quietly(context.bot, message.chat_id, message.message_id)
        try:
            await handler(update, context, db, draft, message.text.strip())
        except PromptError as error:
            await ask(
                context.bot,
                draft,
                draft.data["question"],
                owner=update.effective_user,
                field=draft.data["awaiting"],
                error=str(error),
            )
        else:
            await delete_message_quietly(context.bot, draft.chat_id, draft.prompt_message_id)
            if not is_deleted(db, draft):
                draft.prompt_message_id = None
                update_data(draft, awaiting=None, question=None)
        await db.commit()


def register(application: Application) -> None:
    application.add_handler(
        MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, handle_reply)
    )
