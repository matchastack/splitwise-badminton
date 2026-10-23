from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Update
from telegram.ext import ContextTypes

from badminton.bot import prompts
from badminton.db.models import Draft
from badminton.domain.timeutil import utc_now
from badminton.services.drafts import create_draft
from tests.conftest import BotHarness
from tests.fake_telegram import GROUP_ID, message, text_update, user

BOB = user(222, "bob")
CAROL = user(333, "carol")
answers: list[str] = []


@prompts.on_reply("test")
async def record_answer(
    _update: Update, _context: ContextTypes.DEFAULT_TYPE, _db: AsyncSession, draft: Draft, text: str
) -> None:
    if not text.isdigit():
        raise prompts.PromptError("Enter a number")
    answers.append(f"{draft.data['awaiting']}={text}")


@pytest.fixture
async def asked(bot: BotHarness) -> AsyncIterator[dict]:
    """A draft owned by bob with an open prompt; yields the prompt message."""
    answers.clear()
    async with bot.sessionmaker() as db:
        draft = await create_draft(
            db, kind="test", chat_id=GROUP_ID, user_id=BOB["id"], now=utc_now()
        )
        owner = Update.de_json(text_update("x", sender=BOB), bot.application.bot).effective_user
        await prompts.ask(bot.application.bot, draft, "how many?", owner=owner, field="hours")
        await db.commit()
    yield bot.telegram.last("sendMessage").message


async def test_prompt_is_a_selective_force_reply(bot: BotHarness, asked: dict) -> None:
    params = bot.telegram.last("sendMessage").params

    assert params["reply_markup"] == {"force_reply": True, "selective": True}
    assert "tg://user?id=222" in params["text"]
    assert params["text"].endswith("how many?")


async def test_owner_reply_is_handled_then_cleaned_up(bot: BotHarness, asked: dict) -> None:
    reply = message(sender=BOB, text=" 3 ", reply_to=asked)

    await bot.send({"update_id": 1, "message": reply})

    assert answers == ["hours=3"]
    deleted = [call.params["message_id"] for call in bot.telegram.calls_to("deleteMessage")]
    assert deleted == [reply["message_id"], asked["message_id"]]
    async with bot.sessionmaker() as db:
        draft = (await db.scalars(select(Draft))).one()
        assert draft.prompt_message_id is None
        assert "awaiting" not in draft.data


async def test_invalid_answer_asks_again(bot: BotHarness, asked: dict) -> None:
    await bot.send({"update_id": 1, "message": message(sender=BOB, text="three", reply_to=asked)})

    assert answers == []
    reprompt = bot.telegram.last("sendMessage").params["text"]
    assert reprompt.startswith("⚠️ Enter a number\n")
    assert reprompt.endswith("how many?")
    new_prompt = bot.telegram.last("sendMessage").message
    await bot.send(
        {"update_id": 2, "message": message(sender=BOB, text="4", reply_to=new_prompt)}
    )
    assert answers == ["hours=4"]


async def test_replies_from_others_are_removed(bot: BotHarness, asked: dict) -> None:
    bot.telegram.clear()
    intruder = message(sender=CAROL, text="5", reply_to=asked)

    await bot.send({"update_id": 1, "message": intruder})

    assert answers == []
    assert [call.params["message_id"] for call in bot.telegram.calls] == [intruder["message_id"]]


async def test_replies_to_other_messages_are_removed(bot: BotHarness, asked: dict) -> None:
    bot.telegram.clear()
    reply = message(sender=BOB, text="5", reply_to=message(sender=CAROL, text="hello"))

    await bot.send({"update_id": 1, "message": reply})

    assert answers == []
    assert [call.params["message_id"] for call in bot.telegram.calls] == [reply["message_id"]]
