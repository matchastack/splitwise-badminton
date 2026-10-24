import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from badminton.db.engine import create_sessionmaker
from badminton.db.models import Draft
from badminton.services.drafts import (
    DRAFT_TTL,
    cleanup_expired_drafts,
    create_draft,
    delete_draft,
    find_by_prompt,
    get_draft,
    show_draft,
    update_data,
)
from badminton.services.tick import TickContext
from tests.conftest import TEST_SETTINGS, BotHarness
from tests.fake_telegram import GROUP_ID

NOW = datetime(2026, 10, 21, 2, 0, tzinfo=UTC)


async def test_draft_lifecycle(db: AsyncSession) -> None:
    draft = await create_draft(
        db, kind="booking", chat_id=GROUP_ID, user_id=222, now=NOW, data={"a": 1}
    )
    draft.prompt_message_id = 55
    update_data(draft, b=2)
    await db.commit()

    assert draft.expires_at == NOW + DRAFT_TTL
    assert (await get_draft(db, draft.id, now=NOW)).data == {"a": 1, "b": 2}
    assert await find_by_prompt(db, chat_id=GROUP_ID, prompt_message_id=55, now=NOW) is draft
    assert await find_by_prompt(db, chat_id=GROUP_ID, prompt_message_id=56, now=NOW) is None

    later = NOW + DRAFT_TTL
    assert await get_draft(db, draft.id, now=later) is None
    assert await find_by_prompt(db, chat_id=GROUP_ID, prompt_message_id=55, now=later) is None


async def test_a_second_tap_waits_for_the_first_then_finds_the_draft_gone(
    engine: AsyncEngine,
) -> None:
    sessionmaker = create_sessionmaker(engine)
    async with sessionmaker() as db:
        draft_id = (await create_draft(db, kind="shuttle", chat_id=GROUP_ID, user_id=1, now=NOW)).id
        await db.commit()

    async with sessionmaker() as first, sessionmaker() as second:
        draft = await get_draft(first, draft_id, now=NOW, lock=True)
        waiting = asyncio.create_task(get_draft(second, draft_id, now=NOW, lock=True))
        await asyncio.sleep(0.3)
        assert not waiting.done()  # held by the first tap's row lock
        await first.delete(draft)  # the first tap finishes the wizard
        await first.commit()

        assert await waiting is None  # so it answers like an expired form


async def test_a_second_answer_waits_for_the_first_then_finds_the_prompt_closed(
    engine: AsyncEngine,
) -> None:
    sessionmaker = create_sessionmaker(engine)
    async with sessionmaker() as db:
        draft = await create_draft(db, kind="settle", chat_id=GROUP_ID, user_id=1, now=NOW)
        draft.prompt_message_id = 55
        await db.commit()
    find = {"chat_id": GROUP_ID, "prompt_message_id": 55, "now": NOW, "lock": True}

    async with sessionmaker() as first, sessionmaker() as second:
        draft = await find_by_prompt(first, **find)
        waiting = asyncio.create_task(find_by_prompt(second, **find))
        await asyncio.sleep(0.3)
        assert not waiting.done()
        draft.prompt_message_id = None  # the first answer closes the prompt
        await first.commit()

        assert await waiting is None


async def test_deleting_a_draft_removes_its_open_prompt(bot: BotHarness) -> None:
    async with bot.sessionmaker() as db:
        draft = await create_draft(db, kind="booking", chat_id=GROUP_ID, user_id=222, now=NOW)
        draft.prompt_message_id = 55
        await db.commit()

        await delete_draft(bot.application.bot, db, draft)
        await db.commit()

        assert await db.scalar(select(func.count()).select_from(Draft)) == 0
    deleted = bot.telegram.last("deleteMessage").params
    assert (deleted["chat_id"], deleted["message_id"]) == (GROUP_ID, 55)


def card_draft() -> Draft:
    return Draft(kind="settle", chat_id=GROUP_ID, user_id=1, message_id=42, data={})


CARD = InlineKeyboardMarkup([[InlineKeyboardButton("✖ Cancel", callback_data="st:1:x:")]])


async def test_show_draft_edits_the_card(bot: BotHarness) -> None:
    await show_draft(bot.application.bot, card_draft(), "💸 Who paid?", CARD)

    edit = bot.telegram.last("editMessageText").params
    assert (edit["chat_id"], edit["message_id"], edit["text"]) == (GROUP_ID, 42, "💸 Who paid?")
    assert edit["reply_markup"] == CARD.to_dict()


async def test_show_draft_ignores_an_unchanged_card(bot: BotHarness) -> None:
    bot.telegram.fail("editMessageText", 400, "Bad Request: message is not modified")

    await show_draft(bot.application.bot, card_draft(), "💸 Who paid?", CARD)

    assert bot.telegram.calls_to("editMessageText")  # it was tried, and refused


async def test_show_draft_raises_other_errors(bot: BotHarness) -> None:
    bot.telegram.fail("editMessageText", 400, "Bad Request: message to edit not found")

    with pytest.raises(BadRequest, match="Message to edit not found"):
        await show_draft(bot.application.bot, card_draft(), "💸 Who paid?", CARD)


async def test_update_data_removes_none_values(db: AsyncSession) -> None:
    draft = await create_draft(
        db, kind="booking", chat_id=GROUP_ID, user_id=1, now=NOW, data={"keep": 1, "drop": 2}
    )

    update_data(draft, drop=None, new=3)

    assert draft.data == {"keep": 1, "new": 3}


async def test_cleanup_deletes_expired_drafts_and_their_messages(bot: BotHarness) -> None:
    async with bot.sessionmaker() as db:
        old = await create_draft(
            db,
            kind="booking",
            chat_id=GROUP_ID,
            user_id=1,
            now=NOW - DRAFT_TTL,
            message_id=10,
            data={"owned_message_ids": [9]},
        )
        old.prompt_message_id = 11
        await create_draft(db, kind="booking", chat_id=GROUP_ID, user_id=2, now=NOW, message_id=20)
        await db.commit()
    bot.telegram.fail("deleteMessage", 400, "Bad Request: message to delete not found")

    context = TickContext(bot.application.bot, TEST_SETTINGS, bot.sessionmaker, NOW)
    await cleanup_expired_drafts(context)

    deleted = [call.params["message_id"] for call in bot.telegram.calls_to("deleteMessage")]
    assert deleted == [10, 11, 9]  # card, prompt, owned; the first failed and was ignored
    async with bot.sessionmaker() as db:
        remaining = await db.scalars(select(Draft.message_id))
        assert remaining.all() == [20]
        assert await db.scalar(select(func.count()).select_from(Draft)) == 1


async def test_cleanup_carries_on_when_the_bot_left_the_chat(bot: BotHarness) -> None:
    async with bot.sessionmaker() as db:
        await create_draft(
            db, kind="booking", chat_id=GROUP_ID, user_id=1, now=NOW - DRAFT_TTL, message_id=10
        )
        await db.commit()
    bot.telegram.fail("deleteMessage", 403, "Forbidden: bot was kicked from the supergroup chat")

    context = TickContext(bot.application.bot, TEST_SETTINGS, bot.sessionmaker, NOW)
    await cleanup_expired_drafts(context)

    async with bot.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(Draft)) == 0
