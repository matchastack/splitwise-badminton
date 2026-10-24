from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from badminton.db.models import Draft
from badminton.services.drafts import (
    DRAFT_TTL,
    cleanup_expired_drafts,
    create_draft,
    delete_draft,
    find_by_prompt,
    get_draft,
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
            data={"photo_message_id": 9},
        )
        old.prompt_message_id = 11
        await create_draft(db, kind="booking", chat_id=GROUP_ID, user_id=2, now=NOW, message_id=20)
        await db.commit()
    bot.telegram.fail("deleteMessage", 400, "Bad Request: message to delete not found")

    context = TickContext(bot.application.bot, TEST_SETTINGS, bot.sessionmaker, NOW)
    await cleanup_expired_drafts(context)

    deleted = [call.params["message_id"] for call in bot.telegram.calls_to("deleteMessage")]
    assert deleted == [10, 9, 11]  # card, screenshot, prompt; the first failed and was ignored
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
