"""Shared fixtures.

Database tests need ``TEST_DATABASE_URL`` pointing at a throwaway Postgres: the
schema is dropped and rebuilt from the migrations once per run, and every table
is truncated before each test. Without it those tests are skipped.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from badminton.config import normalise_database_url
from badminton.db.engine import create_engine, create_sessionmaker
from badminton.db.models import Base

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
