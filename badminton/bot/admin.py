"""Admin-only handlers (FR-ADM-1)."""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes

from badminton.bot.context import settings_of

NOT_ADMIN = "Only the admin can do that."

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


def admin_only(handler: Handler) -> Handler:
    """Run ``handler`` only for users in ADMIN_TELEGRAM_IDS; refuse everyone else."""

    @functools.wraps(handler)
    async def guarded(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user is not None and settings_of(context).is_admin(user.id):
            await handler(update, context)
        elif update.callback_query is not None:
            await update.callback_query.answer(NOT_ADMIN, show_alert=True)
        elif update.effective_message is not None:
            await update.effective_message.reply_text(NOT_ADMIN)

    return guarded
