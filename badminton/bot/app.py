"""python-telegram-bot application factory.

The app runs in webhook mode on serverless functions, so there is no Updater:
each request builds an application, processes one update and shuts it down.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram.constants import ParseMode
from telegram.ext import Application, ApplicationBuilder, Defaults
from telegram.request import BaseRequest

from badminton.bot.context import OCR_KEY, SESSIONMAKER_KEY, SETTINGS_KEY
from badminton.bot.handlers import register_handlers
from badminton.config import Settings
from badminton.services import ocr


def build_application(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    request: BaseRequest | None = None,
    polling: bool = False,
    read_text: Callable[[bytes], str] = ocr.read_text,
) -> Application:
    """Build the bot application.

    ``request`` replaces the HTTP transport (tests). ``polling`` keeps the Updater so
    the app can long-poll during local development instead of using the webhook.
    ``read_text`` reads booking screenshots (tests replace the OCR).
    """
    builder = (
        ApplicationBuilder().token(settings.bot_token).defaults(Defaults(parse_mode=ParseMode.HTML))
    )
    if not polling:
        builder = builder.updater(None)
    if request is not None:
        builder = builder.request(request)
    application = builder.build()
    application.bot_data[SETTINGS_KEY] = settings
    application.bot_data[SESSIONMAKER_KEY] = sessionmaker
    application.bot_data[OCR_KEY] = read_text
    register_handlers(application)
    return application
