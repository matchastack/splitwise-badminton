"""The scheduled tick (FR-OPS-2).

Vercel Hobby runs the cron at most once a day (constraint C-1), and an optional
external scheduler may call it more often, so every step must be idempotent and
safe to run any number of times.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import Bot

from badminton.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TickContext:
    bot: Bot
    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    now: datetime


TickStep = Callable[[TickContext], Awaitable[None]]


async def run_tick(context: TickContext, steps: Sequence[tuple[str, TickStep]]) -> dict[str, str]:
    """Run each step in order. A failing step is logged and does not stop the rest."""
    results: dict[str, str] = {}
    for name, step in steps:
        try:
            await step(context)
        except Exception:
            logger.exception("Tick step %s failed", name)
            results[name] = "error"
        else:
            results[name] = "ok"
    return results
