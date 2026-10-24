"""Shared fixtures.

Database tests need ``TEST_DATABASE_URL`` pointing at a throwaway Postgres: the
schema is dropped and rebuilt from the migrations once per run, and every table
is truncated before each test. Without it those tests are skipped.

The ``bot`` fixture runs the real application against ``FakeTelegram``. Its OCR reads
an image's bytes as text, so a test puts a screenshot's text in ``telegram.files``.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from telegram import Update
from telegram.ext import Application, ContextTypes

from badminton.bot.app import build_application
from badminton.bot.keyboards import decode
from badminton.config import Settings, normalise_database_url
from badminton.db.engine import create_engine, create_sessionmaker
from badminton.db.models import Base
from badminton.domain.timeutil import utc_now
from badminton.services.drafts import get_draft
from tests.fake_telegram import ADMIN_ID, GROUP_ID, FakeTelegram

ROOT = Path(__file__).resolve().parents[1]


def alembic_config(database_url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    return normalise_database_url(url)


@pytest.fixture(scope="session")
def migrated_database_url(database_url: str) -> str:
    engine = sa.create_engine(database_url, poolclass=sa.pool.NullPool)
    with engine.begin() as connection:
        connection.execute(sa.text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    engine.dispose()
    command.upgrade(alembic_config(database_url), "head")
    return database_url


@pytest.fixture
async def engine(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(migrated_database_url)
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    async with engine.begin() as connection:
        await connection.execute(sa.text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield engine
    await engine.dispose()


@pytest.fixture
async def db(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with create_sessionmaker(engine)() as session:
        yield session


# Bot-flow harness -----------------------------------------------------------------


TEST_SETTINGS = Settings.from_env(
    {
        "BOT_TOKEN": "123:test-token",
        "WEBHOOK_SECRET": "test-secret",
        "DATABASE_URL": "postgresql://unused@localhost/unused",
        "CRON_SECRET": "test-cron",
        "ADMIN_TELEGRAM_IDS": str(ADMIN_ID),
        "GROUP_CHAT_ID": str(GROUP_ID),
    }
)


@dataclass
class BotHarness:
    """The real application wired to a fake Telegram API and the test database."""

    application: Application
    telegram: FakeTelegram
    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    errors: list[BaseException] = field(default_factory=list)

    async def send(self, update: dict[str, Any]) -> None:
        """Process one incoming update. Any handler error is re-raised here."""
        self.errors.clear()
        await self.application.process_update(Update.de_json(update, self.application.bot))
        if self.errors:
            raise self.errors[0]

    async def double_tap(self, update: dict[str, Any]) -> None:
        """Process a button tap twice at once, as Telegram may deliver a double-tap. The
        card's draft is held until both requests are under way, so neither can finish
        before the other starts."""
        self.errors.clear()
        draft_id = decode(update["callback_query"]["data"]).draft_id
        async with self.sessionmaker() as holder:
            await get_draft(holder, draft_id, now=utc_now(), lock=True)
            taps = [
                asyncio.create_task(
                    self.application.process_update(Update.de_json(update, self.application.bot))
                )
                for _ in range(2)
            ]
            await asyncio.sleep(0.3)
            assert not any(tap.done() for tap in taps)
        await asyncio.gather(*taps)
        if self.errors:
            raise self.errors[0]


@pytest.fixture
async def bot(engine: AsyncEngine) -> AsyncIterator[BotHarness]:
    telegram = FakeTelegram()
    sessionmaker = create_sessionmaker(engine)
    application = build_application(
        TEST_SETTINGS, sessionmaker, request=telegram, read_text=bytes.decode
    )
    harness = BotHarness(application, telegram, TEST_SETTINGS, sessionmaker)

    async def record_error(_update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        if context.error is not None:
            harness.errors.append(context.error)

    application.add_error_handler(record_error)
    async with application:
        yield harness
