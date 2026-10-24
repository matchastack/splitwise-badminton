import pytest

from badminton.domain.timeutil import utc_now
from badminton.services.drafts import create_draft
from tests.conftest import BotHarness
from tests.fake_telegram import (
    ADMIN_ID,
    GROUP_ID,
    message,
    message_update,
    photo_message,
    text_update,
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


def deleted_ids(bot: BotHarness) -> list[int]:
    return [call.params["message_id"] for call in bot.telegram.calls_to("deleteMessage")]


@pytest.mark.parametrize(
    "incoming",
    [
        message(sender=BOB, text="anyone playing sunday?"),
        message(sender=BOB, sticker=STICKER),
        message(sender=BOB, text="/unknown"),
        message(sender=BOB, text="/start"),  # only handled in DMs
        message(sender=BOB, text="/help@some_other_bot"),
        photo_message(sender=BOB),
        photo_message(sender=BOB, caption="look at this court"),
    ],
    ids=[
        "text",
        "sticker",
        "unknown-command",
        "dm-only-command",
        "other-bot",
        "photo",
        "captioned",
    ],
)
async def test_deletes_member_messages_nothing_handled(bot: BotHarness, incoming: dict) -> None:
    await bot.send(message_update(incoming))

    assert deleted_ids(bot) == [incoming["message_id"]]


@pytest.mark.parametrize(
    "incoming",
    [
        message(sender=ALICE, text="Courts are booked for Sunday"),
        message(sender=BOB, new_chat_members=[CAROL]),
        message(sender=BOB, left_chat_member=CAROL),
        message(
            sender=user(1087968824, "Group"),
            sender_chat={"id": GROUP_ID, "type": "supergroup", "title": "Badminton"},
            text="anonymous admin",
        ),
        photo_message(sender=BOB, caption="/book"),
        photo_message(sender=BOB, caption="/book@badminton_bot 4 Oct"),
    ],
    ids=["admin", "join", "leave", "anonymous-admin", "book-photo", "book-photo-addressed"],
)
async def test_keeps_exempt_messages(bot: BotHarness, incoming: dict) -> None:
    await bot.send(message_update(incoming))

    assert deleted_ids(bot) == []


async def test_keeps_handled_commands(bot: BotHarness) -> None:
    await bot.send(text_update("/help", sender=BOB))

    assert deleted_ids(bot) == []
    assert bot.telegram.calls_to("sendMessage")


async def test_does_not_moderate_private_chats(bot: BotHarness) -> None:
    await bot.send(text_update("hello bot", sender=BOB, chat_id=BOB["id"]))

    assert bot.telegram.calls == []


async def test_reply_to_someone_elses_prompt_is_deleted(bot: BotHarness) -> None:
    async with bot.sessionmaker() as db:
        draft = await create_draft(
            db, kind="booking", chat_id=GROUP_ID, user_id=BOB["id"], now=utc_now()
        )
        draft.prompt_message_id = 777
        await db.commit()
    prompt = message(
        sender={"id": 9000, "is_bot": True, "first_name": "Bot"}, text="?", message_id=777
    )
    intruder = message(sender=CAROL, text="12", reply_to=prompt)

    await bot.send(message_update(intruder))

    assert deleted_ids(bot) == [intruder["message_id"]]


async def test_missing_delete_rights_is_not_an_error(bot: BotHarness) -> None:
    bot.telegram.fail("deleteMessage", 400, "Bad Request: message can't be deleted")

    await bot.send(text_update("hi all", sender=BOB))

    assert len(bot.telegram.calls_to("deleteMessage")) == 1
