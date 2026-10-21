"""Register the webhook and publish the command menu (FR-OPS-3).

Run after each deployment whose URL or commands changed:

    uv run --env-file .env scripts/setup_telegram.py --base-url https://<app>.vercel.app
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram import Bot, BotCommandScopeChat, BotCommandScopeChatMember, BotCommandScopeDefault

from badminton.bot.commands import ADMIN_COMMANDS, ALLOWED_UPDATES, MEMBER_COMMANDS
from badminton.config import Settings

WEBHOOK_PATH = "/api/telegram"


def webhook_url(base_url: str) -> str:
    if not base_url.startswith("https://"):
        raise ValueError("Telegram webhooks must use https://")
    return base_url.rstrip("/") + WEBHOOK_PATH


async def configure(
    bot: Bot, settings: Settings, base_url: str, *, drop_pending_updates: bool = False
) -> None:
    await bot.set_webhook(
        url=webhook_url(base_url),
        secret_token=settings.webhook_secret,
        allowed_updates=list(ALLOWED_UPDATES),
        drop_pending_updates=drop_pending_updates,
    )
    await bot.set_my_commands(MEMBER_COMMANDS, scope=BotCommandScopeDefault())
    for admin_id in sorted(settings.admin_ids):
        await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(admin_id))
        if settings.group_chat_id is not None:
            scope = BotCommandScopeChatMember(settings.group_chat_id, admin_id)
            await bot.set_my_commands(ADMIN_COMMANDS, scope=scope)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="Public https URL of the deployment; the webhook is <base-url>/api/telegram",
    )
    parser.add_argument(
        "--drop-pending-updates",
        action="store_true",
        help="Discard updates Telegram queued while no webhook was reachable",
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    settings = Settings.from_env()
    async with Bot(settings.bot_token) as bot:
        await configure(
            bot, settings, args.base_url, drop_pending_updates=args.drop_pending_updates
        )
        info = await bot.get_webhook_info()
    print(f"Webhook set to {info.url} (pending updates: {info.pending_update_count})")


if __name__ == "__main__":
    asyncio.run(main())
