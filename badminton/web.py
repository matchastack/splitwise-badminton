"""HTTP entry points: the Telegram webhook and the scheduled tick (requirements §6.2)."""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import Update
from telegram.ext import Application
from telegram.request import BaseRequest

from badminton.bot.app import build_application
from badminton.config import Settings, get_settings
from badminton.db.engine import create_engine, create_sessionmaker
from badminton.services import schedule
from badminton.services.tick import TickContext, run_tick

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Runtime:
    """Process-wide dependencies, overridable in tests."""

    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    telegram_request: BaseRequest | None = None

    def application(self) -> Application:
        return build_application(self.settings, self.sessionmaker, request=self.telegram_request)


@lru_cache(maxsize=1)
def get_runtime() -> Runtime:
    settings = get_settings()
    return Runtime(settings, create_sessionmaker(create_engine(settings.database_url)))


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]

app = FastAPI(title="splitwise-badminton", docs_url=None, redoc_url=None, openapi_url=None)


def _secret_matches(given: str | None, expected: str) -> bool:
    return given is not None and hmac.compare_digest(given.encode(), expected.encode())


@app.post("/api/telegram")
async def telegram_webhook(
    request: Request,
    runtime: RuntimeDep,
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    """Process one Telegram update (FR-OPS-1).

    Once authenticated, always answer 200 so Telegram doesn't keep retrying an
    update whose handler failed; the failure is logged instead.
    """
    if not _secret_matches(x_telegram_bot_api_secret_token, runtime.settings.webhook_secret):
        raise HTTPException(status_code=401)
    try:
        payload = await request.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected a Telegram update object")

    application = runtime.application()
    async with application:
        try:
            await application.process_update(Update.de_json(payload, application.bot))
        except Exception:
            logger.exception("Failed to process update %s", payload.get("update_id"))
    return {"ok": True}


@app.api_route("/api/cron", methods=["GET", "POST"])
async def cron(
    runtime: RuntimeDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Run the scheduled tick (FR-OPS-2). Vercel sends ``Authorization: Bearer $CRON_SECRET``."""
    if not _secret_matches(authorization, f"Bearer {runtime.settings.cron_secret}"):
        raise HTTPException(status_code=401)

    application = runtime.application()
    async with application:
        context = TickContext(
            bot=application.bot,
            settings=runtime.settings,
            sessionmaker=runtime.sessionmaker,
            now=datetime.now(UTC),
        )
        results = await run_tick(context, schedule.default_steps())
    return {"ok": all(result == "ok" for result in results.values()), "steps": results}
