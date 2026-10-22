import subprocess
import sys
from pathlib import Path

import pytest
from telegram import Bot
from telegram.error import BadRequest

from badminton.bot.app import build_application
from badminton.bot.commands import ADMIN_COMMANDS, MEMBER_COMMANDS
from scripts.setup_telegram import configure, webhook_url
from tests.conftest import TEST_SETTINGS
from tests.fake_telegram import ADMIN_ID, GROUP_ID, FakeTelegram

ROOT = Path(__file__).resolve().parents[1]


def command_names(commands: object) -> list[str]:
    return [command["command"] for command in commands]  # type: ignore[attr-defined]


async def test_configure_sets_webhook_and_command_menus() -> None:
    telegram = FakeTelegram()
    async with Bot(TEST_SETTINGS.bot_token, request=telegram) as bot:
        await configure(
            bot, TEST_SETTINGS, "https://example.vercel.app/", drop_pending_updates=True
        )

    webhook = telegram.last("setWebhook").params
    assert webhook["url"] == "https://example.vercel.app/api/telegram"
    assert webhook["secret_token"] == TEST_SETTINGS.webhook_secret
    assert webhook["allowed_updates"] == ["message", "callback_query"]
    assert webhook["drop_pending_updates"] is True

    menus = [
        (c.params["scope"], command_names(c.params["commands"]))
        for c in telegram.calls_to("setMyCommands")
    ]
    members = [c.command for c in MEMBER_COMMANDS]
    admins = [c.command for c in ADMIN_COMMANDS]
    assert menus == [
        ({"type": "default"}, members),
        ({"type": "chat", "chat_id": ADMIN_ID}, admins),
        ({"type": "chat_member", "chat_id": GROUP_ID, "user_id": ADMIN_ID}, admins),
    ]


async def test_configure_reports_an_admin_who_has_not_started_the_bot() -> None:
    telegram = FakeTelegram()
    telegram.fail("setMyCommands", 400, "Bad Request: chat not found", chat_id=ADMIN_ID)
    async with Bot(TEST_SETTINGS.bot_token, request=telegram) as bot:
        refused = await configure(bot, TEST_SETTINGS, "https://example.vercel.app")

    assert refused == [f"admin {ADMIN_ID}, private chat: Chat not found"]
    assert telegram.last("setWebhook").params["url"] == "https://example.vercel.app/api/telegram"
    scopes = [c.params["scope"] for c in telegram.calls_to("setMyCommands")]
    assert {"type": "chat_member", "chat_id": GROUP_ID, "user_id": ADMIN_ID} in scopes


async def test_configure_still_raises_when_the_webhook_is_refused() -> None:
    telegram = FakeTelegram()
    telegram.fail("setWebhook", 400, "Bad Request: bad webhook: HTTPS url must be provided")
    async with Bot(TEST_SETTINGS.bot_token, request=telegram) as bot:
        with pytest.raises(BadRequest, match=r"(?i)bad webhook"):
            await configure(bot, TEST_SETTINGS, "https://example.vercel.app")


def test_webhook_url_requires_https() -> None:
    with pytest.raises(ValueError, match="https"):
        webhook_url("http://example.com")


def test_polling_application_keeps_its_updater() -> None:
    application = build_application(TEST_SETTINGS, None, polling=True)  # type: ignore[arg-type]

    assert application.updater is not None
    assert build_application(TEST_SETTINGS, None).updater is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("script", "flag"),
    [("setup_telegram.py", "--base-url"), ("dev_polling.py", "--tick-interval")],
)
def test_scripts_document_their_arguments(script: str, flag: str) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert flag in result.stdout
