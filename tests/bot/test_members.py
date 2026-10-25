import dataclasses

import pytest
from sqlalchemy import select
from telegram import Update
from telegram.ext import CommandHandler

from badminton.bot.app import build_application
from badminton.bot.commands import ADMIN_COMMANDS
from badminton.db.models import Member
from badminton.services.members import mention_html
from tests.conftest import TEST_SETTINGS, BotHarness
from tests.fake_telegram import ADMIN_ID, GROUP_ID, FakeTelegram, text_update, user

ALICE = user(ADMIN_ID, "alice", "alice_handle")
BOB = user(222, "Bob  Smith")
OTHER_GROUP = -1009999


async def member_by_telegram_id(bot: BotHarness, telegram_id: int) -> Member | None:
    async with bot.sessionmaker() as db:
        return await db.scalar(select(Member).where(Member.telegram_user_id == telegram_id))


async def test_start_registers_member_and_enables_dms(bot: BotHarness) -> None:
    await bot.send(text_update("/start", sender=BOB, chat_id=BOB["id"]))

    member = await member_by_telegram_id(bot, BOB["id"])
    assert member is not None
    assert member.display_name == "Bob Smith"
    assert member.dm_enabled
    reply = bot.telegram.last("sendMessage").params
    assert reply["chat_id"] == BOB["id"]
    assert "Hi <b>Bob Smith</b>" in reply["text"]
    assert reply["parse_mode"] == "HTML"


async def test_start_in_the_group_is_not_handled(bot: BotHarness) -> None:
    await bot.send(text_update("/start", sender=BOB))

    assert [call.method for call in bot.telegram.calls] == ["deleteMessage"]
    assert await member_by_telegram_id(bot, BOB["id"]) is None


async def test_start_says_to_send_commands_here(bot: BotHarness) -> None:
    await bot.send(text_update("/start", sender=BOB, chat_id=BOB["id"]))

    assert (
        "Send me your commands here, and I'll post what you record in the group."
        in (bot.telegram.sent_texts(BOB["id"])[0])
    )


async def test_name_sticks_and_username_refreshes(bot: BotHarness) -> None:
    await bot.send(text_update("/name Bob <T>", sender=BOB, chat_id=BOB["id"]))
    renamed_user = {**BOB, "username": "bob_t", "first_name": "Someone else"}
    await bot.send(text_update("/start", sender=renamed_user, chat_id=BOB["id"]))

    member = await member_by_telegram_id(bot, BOB["id"])
    assert member.display_name == "Bob <T>"
    assert member.username == "bob_t"
    assert "I'll call you <b>Bob &lt;T&gt;</b>" in bot.telegram.sent_texts(BOB["id"])[0]


@pytest.mark.parametrize(
    ("command", "error"),
    [("/name", "Usage: /name &lt;your name&gt;"), ("/name " + "x" * 33, "at most 32 characters")],
)
async def test_name_rejects_bad_input(bot: BotHarness, command: str, error: str) -> None:
    await bot.send(text_update(command, sender=BOB, chat_id=BOB["id"]))

    assert error in bot.telegram.sent_texts()[0]


async def test_help_shows_admin_commands_only_to_admin(bot: BotHarness) -> None:
    await bot.send(text_update("/help", sender=BOB, chat_id=BOB["id"]))
    await bot.send(text_update("/help", sender=ALICE, chat_id=ALICE["id"]))

    member_help, admin_help = bot.telegram.sent_texts()
    assert "/court — " in member_help
    assert "/edit" not in member_help
    assert "Send these to me here; I post what you record in the group." in member_help
    assert "/edit — " in admin_help
    assert admin_help.count("/court — ") == 1


async def test_chatid_replies_with_the_chat_id(bot: BotHarness) -> None:
    await bot.send(text_update("/chatid", sender=BOB))

    assert bot.telegram.sent_texts() == [f"Chat ID: <code>{GROUP_ID}</code>"]


async def test_foreign_groups_are_ignored(bot: BotHarness) -> None:
    for command in ("/help", "/chatid", "/name Intruder"):
        await bot.send(text_update(command, sender=BOB, chat_id=OTHER_GROUP))

    assert bot.telegram.calls == []


async def test_admin_chatid_works_in_another_group(bot: BotHarness) -> None:
    for command in ("/chatid", "/help"):
        await bot.send(text_update(command, sender=ALICE, chat_id=OTHER_GROUP))

    assert [call.params["text"] for call in bot.telegram.calls] == [
        f"Chat ID: <code>{OTHER_GROUP}</code>"
    ]


async def test_chatid_works_anywhere_before_setup(bot: BotHarness) -> None:
    telegram = FakeTelegram()
    unconfigured = dataclasses.replace(TEST_SETTINGS, group_chat_id=None)
    application = build_application(unconfigured, bot.sessionmaker, request=telegram)

    async with application:
        for command in ("/chatid", "/help"):
            update = text_update(command, sender=BOB, chat_id=OTHER_GROUP)
            await application.process_update(Update.de_json(update, application.bot))

    assert [call.params["text"] for call in telegram.calls] == [
        f"Chat ID: <code>{OTHER_GROUP}</code>"
    ]


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        (
            Member(display_name="alice", username="alice_handle", telegram_user_id=1),
            "@alice_handle",
        ),
        (
            Member(display_name="Carol <3", telegram_user_id=7),
            '<a href="tg://user?id=7">Carol &lt;3</a>',
        ),
        (Member(display_name="grace"), "grace"),
    ],
)
def test_mention_html(member: Member, expected: str) -> None:
    assert mention_html(member) == expected


def test_every_menu_command_has_a_handler(bot: BotHarness) -> None:
    handled = {
        command
        for handlers in bot.application.handlers.values()
        for handler in handlers
        if isinstance(handler, CommandHandler)
        for command in handler.commands
    }

    assert {command.command for command in ADMIN_COMMANDS} <= handled
