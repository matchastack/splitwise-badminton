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
from tests.fake_telegram import ADMIN_ID, BOT_USER, GROUP_ID, message, text_update, user

ALICE = user(ADMIN_ID, "alice")
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


async def ask_bob(bot: BotHarness, chat_id: int) -> dict:
    """A draft owned by bob in ``chat_id`` with an open prompt; returns the prompt message."""
    answers.clear()
    async with bot.sessionmaker() as db:
        draft = await create_draft(
            db, kind="test", chat_id=chat_id, user_id=BOB["id"], now=utc_now()
        )
        owner = Update.de_json(text_update("x", sender=BOB), bot.application.bot).effective_user
        await prompts.ask(bot.application.bot, draft, "how many?", owner=owner, field="hours")
        await db.commit()
    return bot.telegram.last("sendMessage").message


@pytest.fixture
async def asked(bot: BotHarness) -> AsyncIterator[dict]:
    """A prompt in bob's private chat, where every wizard is."""
    yield await ask_bob(bot, BOB["id"])


async def test_prompt_is_a_selective_force_reply(bot: BotHarness, asked: dict) -> None:
    params = bot.telegram.last("sendMessage").params

    assert params["reply_markup"] == {"force_reply": True, "selective": True}
    assert params["text"] == "How many?"  # no need to name bob in his own chat


async def test_a_prompt_in_the_group_names_its_owner(bot: BotHarness) -> None:
    await ask_bob(bot, GROUP_ID)

    text = bot.telegram.last("sendMessage").params["text"]
    assert text == '<a href="tg://user?id=222">bob</a>, how many?'


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
    assert reprompt == "⚠️ Enter a number\nHow many?"
    new_prompt = bot.telegram.last("sendMessage").message
    await bot.send({"update_id": 2, "message": message(sender=BOB, text="4", reply_to=new_prompt)})
    assert answers == ["hours=4"]


async def test_replies_to_other_messages_are_ignored(bot: BotHarness, asked: dict) -> None:
    bot.telegram.clear()
    earlier = message(sender=BOB, text="hello", chat_id=BOB["id"])
    reply = message(sender=BOB, text="5", reply_to=earlier)

    await bot.send({"update_id": 1, "message": reply})

    assert answers == []
    assert bot.telegram.calls == []


@pytest.fixture
def no_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    async def find_by_prompt(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a reply in the group was looked up")

    monkeypatch.setattr(prompts, "find_by_prompt", find_by_prompt)


@pytest.mark.usefixtures("no_lookups")
@pytest.mark.parametrize("sender", [ALICE, CAROL])
async def test_replies_in_the_group_are_not_looked_up(bot: BotHarness, sender: dict) -> None:
    """Prompts are only asked in private chats, so a reply in the group costs no query."""
    bot_message = message(sender=BOT_USER, text="📅 Upcoming sessions")
    reply = message(sender=sender, text="3", reply_to=bot_message)

    await bot.send({"update_id": 1, "message": reply})

    assert bot.telegram.calls == []
