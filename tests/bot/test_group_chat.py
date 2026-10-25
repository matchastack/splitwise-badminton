"""The group is a normal chat (FR-MOD-1, FR-MOD-3): the bot answers its own commands
there with a pointer to the private chat, and leaves everything else alone."""

import pytest

from tests.conftest import BotHarness
from tests.fake_telegram import (
    ADMIN_ID,
    BOT_USER,
    GROUP_ID,
    message,
    message_update,
    photo_message,
    user,
)

ALICE = user(ADMIN_ID, "alice")
BOB = user(222, "bob")
CAROL = user(333, "carol")
STICKER = {
    "file_id": "sticker",
    "file_unique_id": "s",
    "type": "regular",
    "width": 512,
    "height": 512,
    "is_animated": False,
    "is_video": False,
}
CARD = message(sender=BOT_USER, text="📅 Sun 4 Oct, 11:00–13:00 (2h)")


def chat(sender: dict) -> list[dict]:
    """Messages the group sends each other, none of them for the bot."""
    return [
        message(sender=sender, text="anyone playing sunday?"),
        message(sender=sender, sticker=STICKER),
        photo_message(sender=sender),
        photo_message(sender=sender, caption="look at this court"),
        photo_message(sender=sender, caption="/court@some_other_bot"),
        message(sender=sender, text="count me in", reply_to=CARD),
        message(sender=sender, text="/unknown"),
        message(sender=sender, text="/start"),  # only handled in private chats
        message(sender=sender, text="/help@some_other_bot"),
        message(sender=sender, new_chat_members=[CAROL]),
    ]


IDS = [
    "text",
    "sticker",
    "photo",
    "captioned-photo",
    "other-bot-photo",
    "reply",
    "unknown-command",
    "private-only-command",
    "other-bot-command",
    "join",
]


@pytest.mark.parametrize("incoming", chat(BOB), ids=IDS)
async def test_a_members_chat_gets_no_telegram_call(bot: BotHarness, incoming: dict) -> None:
    await bot.send(message_update(incoming))

    assert bot.telegram.calls == []


@pytest.mark.parametrize("incoming", chat(ALICE), ids=IDS)
async def test_the_admins_chat_gets_no_telegram_call(bot: BotHarness, incoming: dict) -> None:
    await bot.send(message_update(incoming))

    assert bot.telegram.calls == []


async def test_a_command_for_the_bot_is_answered_and_kept(bot: BotHarness) -> None:
    await bot.send(message_update(message(sender=BOB, text="/balance")))

    assert [call.method for call in bot.telegram.calls] == ["sendMessage"]
    assert bot.telegram.last("sendMessage").params["chat_id"] == GROUP_ID
