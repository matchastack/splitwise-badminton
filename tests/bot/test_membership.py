import dataclasses
import logging

import pytest
from sqlalchemy import func, select
from telegram import Update

from badminton.bot.app import build_application
from badminton.bot.membership import NOT_A_MEMBER, OPEN_CHAT, PRIVATELY
from badminton.db.models import Member
from tests.conftest import TEST_SETTINGS, BotHarness
from tests.fake_telegram import ADMIN_ID, GROUP_ID, FakeTelegram, text_update, user

ALICE = user(ADMIN_ID, "alice", "alice_handle")
BOB = user(222, "bob")

MEMBER_COMMANDS = ["/start", "/name Bobby", "/help", "/balance", "/upcoming"]


async def in_private(bot: BotHarness, command: str, sender: dict) -> list[str]:
    """Send ``command`` in the sender's private chat; the bot's replies there."""
    bot.telegram.clear()
    await bot.send(text_update(command, sender=sender, chat_id=sender["id"]))
    return bot.telegram.sent_texts(sender["id"])


async def member_count(bot: BotHarness) -> int:
    async with bot.sessionmaker() as db:
        return await db.scalar(select(func.count()).select_from(Member))


@pytest.mark.parametrize("command", MEMBER_COMMANDS)
async def test_outsider_is_refused_and_not_registered(bot: BotHarness, command: str) -> None:
    bot.telegram.set_member_status(BOB["id"], "left")

    assert await in_private(bot, command, BOB) == [NOT_A_MEMBER]
    assert await member_count(bot) == 0
    lookup = bot.telegram.last("getChatMember").params
    assert (int(lookup["chat_id"]), int(lookup["user_id"])) == (GROUP_ID, BOB["id"])


@pytest.mark.parametrize("status", ["member", "administrator", "creator"])
async def test_group_member_is_let_through(bot: BotHarness, status: str) -> None:
    bot.telegram.set_member_status(BOB["id"], status)

    assert await in_private(bot, "/balance", BOB) == ["💰 <b>bob</b>, you're all square."]


async def test_restricted_member_is_let_through(bot: BotHarness) -> None:
    bot.telegram.set_member_status(BOB["id"], "restricted", is_member=True)

    assert await in_private(bot, "/balance", BOB) == ["💰 <b>bob</b>, you're all square."]


@pytest.mark.parametrize(
    ("status", "is_member"), [("restricted", False), ("kicked", True), ("left", True)]
)
async def test_people_no_longer_in_the_group_are_refused(
    bot: BotHarness, status: str, is_member: bool
) -> None:
    bot.telegram.set_member_status(BOB["id"], status, is_member=is_member)

    assert await in_private(bot, "/balance", BOB) == [NOT_A_MEMBER]
    assert await member_count(bot) == 0


@pytest.mark.parametrize(
    ("code", "description"),
    [
        (400, "Bad Request: user not found"),
        (403, "Forbidden: bot was kicked from the supergroup chat"),
    ],
)
async def test_failed_lookup_is_refused_and_logged(
    bot: BotHarness, caplog: pytest.LogCaptureFixture, code: int, description: str
) -> None:
    bot.telegram.fail("getChatMember", code, description)

    with caplog.at_level(logging.WARNING, logger="badminton.bot.membership"):
        assert await in_private(bot, "/start", BOB) == [NOT_A_MEMBER]

    assert await member_count(bot) == 0
    assert f"Could not check whether user {BOB['id']} is in the group" in caplog.text


async def test_admin_is_let_through_without_a_lookup(bot: BotHarness) -> None:
    for command in MEMBER_COMMANDS:
        assert await in_private(bot, command, ALICE) != [NOT_A_MEMBER]
        assert not bot.telegram.calls_to("getChatMember")


@pytest.mark.parametrize(
    "command", ["/court", "/shuttle 76.79", "/balance", "/upcoming", "/name Bobby", "/help"]
)
async def test_member_commands_in_the_group_point_to_the_private_chat(
    bot: BotHarness, command: str
) -> None:
    bot.telegram.set_member_status(BOB["id"], "left")  # it would refuse, if it looked
    sent = text_update(command, sender=BOB)

    await bot.send(sent)

    reply = bot.telegram.last("sendMessage").params
    name = command.split()[0]
    assert (reply["chat_id"], reply["text"]) == (GROUP_ID, PRIVATELY.format(command=name))
    assert reply["reply_parameters"]["message_id"] == sent["message"]["message_id"]
    assert reply["reply_markup"]["inline_keyboard"] == [
        [{"text": OPEN_CHAT, "url": "https://t.me/badminton_bot"}]
    ]
    assert not bot.telegram.calls_to("getChatMember")
    assert await member_count(bot) == 0


async def test_chatid_stays_open_in_private(bot: BotHarness) -> None:
    bot.telegram.set_member_status(BOB["id"], "left")

    assert await in_private(bot, "/chatid", BOB) == [f"Chat ID: <code>{BOB['id']}</code>"]
    assert not bot.telegram.calls_to("getChatMember")


async def test_only_the_admin_gets_through_before_setup(bot: BotHarness) -> None:
    telegram = FakeTelegram()
    unconfigured = dataclasses.replace(TEST_SETTINGS, group_chat_id=None)
    application = build_application(unconfigured, bot.sessionmaker, request=telegram)

    async with application:
        for sender in (BOB, ALICE):
            update = text_update("/balance", sender=sender, chat_id=sender["id"])
            await application.process_update(Update.de_json(update, application.bot))

    assert telegram.sent_texts(BOB["id"]) == [NOT_A_MEMBER]
    assert telegram.sent_texts(ADMIN_ID) == ["💰 <b>alice</b>, you're all square."]
    assert not telegram.calls_to("getChatMember")
