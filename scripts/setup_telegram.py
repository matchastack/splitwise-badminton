"""Register the webhook and publish the command menus (FR-OPS-3).

Commands are sent to the bot in a private chat, so the menus are there: member commands
in every private chat, and admin commands in each admin's. The group has none.

Run after each deployment whose URL or commands changed:

    uv run --env-file .env scripts/setup_telegram.py --base-url https://<app>.vercel.app
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram import (
    Bot,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeChatMember,
    BotCommandScopeDefault,
)
from telegram.error import BadRequest

from badminton.bot.commands import ADMIN_COMMANDS, ALLOWED_UPDATES, MEMBER_COMMANDS
from badminton.config import Settings

WEBHOOK_PATH = "/api/telegram"


def webhook_url(base_url: str) -> str:
    if not base_url.startswith("https://"):
        raise ValueError("Telegram webhooks must use https://")
    return base_url.rstrip("/") + WEBHOOK_PATH


async def configure(
    bot: Bot, settings: Settings, base_url: str, *, drop_pending_updates: bool = False
) -> list[str]:
    """Register the webhook and menus; return a note for each admin menu Telegram refused
    to set or clear.

    The webhook and member menu are what the bot needs to work, so they raise on failure.
    An admin menu is only a convenience, and Telegram refuses it ("Chat not found") until
    that admin has started a chat with the bot, so those failures are reported, not raised.

    Earlier versions published menus for the group: the member menu as the default for
    every chat, and the admin menu for each admin in the group. Both are cleared, so the
    group offers no commands.
    """
    await bot.set_webhook(
        url=webhook_url(base_url),
        secret_token=settings.webhook_secret,
        allowed_updates=list(ALLOWED_UPDATES),
        drop_pending_updates=drop_pending_updates,
    )
    await bot.set_my_commands(MEMBER_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    await bot.delete_my_commands(scope=BotCommandScopeDefault())

    refused: list[str] = []
    for admin_id in sorted(settings.admin_ids):
        try:
            await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(admin_id))
        except BadRequest as error:
            refused.append(f"admin {admin_id}, private chat: {error.message}")
        if settings.group_chat_id is not None:
            try:
                await bot.delete_my_commands(
                    scope=BotCommandScopeChatMember(settings.group_chat_id, admin_id)
                )
            except BadRequest as error:
                refused.append(
                    f"admin {admin_id}, clearing the menu in group {settings.group_chat_id}: "
                    f"{error.message}"
                )
    return refused


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
        refused = await configure(
            bot, settings, args.base_url, drop_pending_updates=args.drop_pending_updates
        )
        info = await bot.get_webhook_info()
    print(f"Webhook set to {info.url} (pending updates: {info.pending_update_count})")
    if refused:
        print("\nThe bot works, but Telegram refused these admin command menus:", file=sys.stderr)
        for note in refused:
            print(f"  - {note}", file=sys.stderr)
        print(
            "Each admin must open the bot in Telegram and press Start (and, to clear an old"
            "\ngroup menu, be in the group with the bot). Then run this script again.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    asyncio.run(main())
