"""Access to the app's dependencies from inside handlers."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram.ext import ContextTypes

from badminton.config import Settings

SETTINGS_KEY = "settings"
SESSIONMAKER_KEY = "sessionmaker"
OCR_KEY = "ocr"


def settings_of(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.bot_data[SETTINGS_KEY]


def db_session(context: ContextTypes.DEFAULT_TYPE) -> AsyncSession:
    """A new database session; use as ``async with db_session(context) as db``."""
    sessionmaker: async_sessionmaker[AsyncSession] = context.bot_data[SESSIONMAKER_KEY]
    return sessionmaker()


def ocr_of(context: ContextTypes.DEFAULT_TYPE) -> Callable[[bytes], str]:
    """Reads the text in an image (``services.ocr.read_text``; tests swap in a fake)."""
    return context.bot_data[OCR_KEY]
