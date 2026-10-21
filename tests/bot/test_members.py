import dataclasses

import pytest
from sqlalchemy import select
from telegram import Update

from badminton.bot.app import build_application
from badminton.db.models import Member
from badminton.services.members import mention_html
from tests.conftest import TEST_SETTINGS, BotHarness
from tests.fake_telegram import ADMIN_ID, GROUP_ID, FakeTelegram, text_update, user

ZY = user(ADMIN_ID, "zy", "zy_handle")
SHAWN = user(222, "Shawn  Tan")
OTHER_GROUP = -1009999


async def member_by_telegram_id(bot: BotHarness, telegram_id: int) -> Member | None:
    async with bot.sessionmaker() as db:
        return await db.scalar(select(Member).where(Member.telegram_user_id == telegram_id))


async def test_start_registers_member_and_enables_dms(bot: BotHarness) -> None:
    await bot.send(text_update("/start", sender=SHAWN, chat_id=SHAWN["id"]))

    member = await member_by_telegram_id(bot, SHAWN["id"])
    assert member is not None
    assert member.display_name == "Shawn Tan"
    assert member.dm_enabled
    reply = bot.telegram.last("sendMessage").params
    assert reply["chat_id"] == SHAWN["id"]
    assert "Hi <b>Shawn Tan</b>" in reply["text"]
    assert reply["parse_mode"] == "HTML"


async def test_start_in_the_group_is_not_handled(bot: BotHarness) -> None:
    await bot.send(text_update("/start", sender=SHAWN))

    assert bot.telegram.calls == []
    assert await member_by_telegram_id(bot, SHAWN["id"]) is None


async def test_name_sticks_and_username_refreshes(bot: BotHarness) -> None:
    await bot.send(text_update("/name Shawn <T>", sender=SHAWN))
    renamed_user = {**SHAWN, "username": "shawn_t", "first_name": "Someone else"}
    await bot.send(text_update("/start", sender=renamed_user, chat_id=SHAWN["id"]))

    member = await member_by_telegram_id(bot, SHAWN["id"])
    assert member.display_name == "Shawn <T>"
    assert member.username == "shawn_t"
    assert "I'll call you <b>Shawn &lt;T&gt;</b>" in bot.telegram.sent_texts(GROUP_ID)[0]


@pytest.mark.parametrize(
    ("command", "error"),
    [("/name", "Usage: /name &lt;your name&gt;"), ("/name " + "x" * 33, "at most 32 characters")],
)
async def test_name_rejects_bad_input(bot: BotHarness, command: str, error: str) -> None:
    await bot.send(text_update(command, sender=SHAWN))

    assert error in bot.telegram.sent_texts()[0]


async def test_help_shows_admin_commands_only_to_admin(bot: BotHarness) -> None:
    await bot.send(text_update("/help", sender=SHAWN))
    await bot.send(text_update("/help", sender=ZY))

    member_help, admin_help = bot.telegram.sent_texts()
    assert "/book — " in member_help
    assert "/edit" not in member_help
    assert "/edit — " in admin_help
    assert admin_help.count("/book — ") == 1


async def test_chatid_replies_with_the_chat_id(bot: BotHarness) -> None:
    await bot.send(text_update("/chatid", sender=SHAWN))

    assert bot.telegram.sent_texts() == [f"Chat ID: <code>{GROUP_ID}</code>"]


async def test_foreign_groups_are_ignored(bot: BotHarness) -> None:
    for command in ("/help", "/chatid", "/name Intruder"):
        await bot.send(text_update(command, sender=SHAWN, chat_id=OTHER_GROUP))

    assert bot.telegram.calls == []


async def test_chatid_works_anywhere_before_setup(bot: BotHarness) -> None:
    telegram = FakeTelegram()
    unconfigured = dataclasses.replace(TEST_SETTINGS, group_chat_id=None)
    application = build_application(unconfigured, bot.sessionmaker, request=telegram)

    async with application:
        for command in ("/chatid", "/help"):
            update = text_update(command, sender=SHAWN, chat_id=OTHER_GROUP)
            await application.process_update(Update.de_json(update, application.bot))

    assert [call.params["text"] for call in telegram.calls] == [
        f"Chat ID: <code>{OTHER_GROUP}</code>"
    ]


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        (Member(display_name="zy", username="zy_handle", telegram_user_id=1), "@zy_handle"),
        (
            Member(display_name="Nic <3", telegram_user_id=7),
            '<a href="tg://user?id=7">Nic &lt;3</a>',
        ),
        (Member(display_name="danish"), "danish"),
    ],
)
def test_mention_html(member: Member, expected: str) -> None:
    assert mention_html(member) == expected
