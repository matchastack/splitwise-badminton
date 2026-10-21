"""Run the bot locally with long polling instead of the webhook.

Polling removes the webhook, so run setup_telegram.py again before going back
to the deployed bot. Use a separate test bot and database where possible:

    uv run --env-file .env scripts/dev_polling.py
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram.ext import Application

from badminton.bot.app import build_application
from badminton.bot.commands import ALLOWED_UPDATES
from badminton.bot.context import SESSIONMAKER_KEY
from badminton.config import Settings
from badminton.db.engine import create_engine, create_sessionmaker
from badminton.services.tick import TickContext, run_tick

logger = logging.getLogger("dev_polling")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--tick-interval",
        type=int,
        default=60,
        metavar="SECONDS",
        help="How often to run the scheduled tick (default: 60). 0 disables it.",
    )
    return parser.parse_args(argv)


async def tick_forever(application: Application, settings: Settings, interval: int) -> None:
    sessionmaker = application.bot_data[SESSIONMAKER_KEY]
    while True:
        context = TickContext(application.bot, settings, sessionmaker, datetime.now(UTC))
        logger.info("Tick: %s", await run_tick(context))
        await asyncio.sleep(interval)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = Settings.from_env()
    sessionmaker = create_sessionmaker(create_engine(settings.database_url))
    application = build_application(settings, sessionmaker, polling=True)

    if args.tick_interval > 0:

        async def start_ticking(app: Application) -> None:
            app.create_task(tick_forever(app, settings, args.tick_interval))

        application.post_init = start_ticking

    application.run_polling(allowed_updates=list(ALLOWED_UPDATES))


if __name__ == "__main__":
    main()
