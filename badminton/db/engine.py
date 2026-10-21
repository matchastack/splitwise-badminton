"""Async engine and session factory for Neon (or any Postgres)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine
from sqlalchemy.pool import NullPool


def create_engine(database_url: str) -> AsyncEngine:
    """Engine suited to serverless: no pooling between requests (Neon's PgBouncer pools),
    and no server-side prepared statements, which PgBouncer's transaction mode breaks."""
    return _create_async_engine(
        database_url,
        poolclass=NullPool,
        connect_args={"prepare_threshold": None},
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
