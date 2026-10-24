import dataclasses
from datetime import date, timedelta

import pytest
from sqlalchemy import select, update
from telegram.error import BadRequest

from badminton.db.models import Session
from badminton.domain.timeutil import at_sgt
from badminton.services.attendance import post_due_cards
from badminton.services.tick import TickContext
from tests.bot.attendance_helpers import make_session
from tests.bot.booking_helpers import BOB, book, future_day
from tests.conftest import TEST_SETTINGS, BotHarness
from tests.fake_telegram import GROUP_ID

SUNDAY = date(2026, 11, 8)
SESSION_START = at_sgt(SUNDAY, 11)


def tick_at(bot: BotHarness, when, settings=TEST_SETTINGS) -> TickContext:
    return TickContext(bot.application.bot, settings, bot.sessionmaker, when)


async def card_ids(bot: BotHarness) -> dict[int, int | None]:
    async with bot.sessionmaker() as db:
        return {s.id: s.card_message_id for s in await db.scalars(select(Session))}


def cards_sent(bot: BotHarness) -> list[str]:
    """Attendance cards Telegram accepted."""
    return [
        call.params["text"]
        for call in bot.telegram.calls_to("sendMessage")
        if call.result is not None
        and call.params["chat_id"] == GROUP_ID
        and "No answer = not playing." in call.params["text"]
    ]


async def test_posts_two_days_before_and_only_once(bot: BotHarness) -> None:
    session_id = await make_session(bot, starts_at=SESSION_START)

    await post_due_cards(tick_at(bot, at_sgt(SUNDAY - timedelta(days=3), 10)))
    assert cards_sent(bot) == []

    friday_morning = at_sgt(SUNDAY - timedelta(days=2), 10)
    await post_due_cards(tick_at(bot, friday_morning))
    await post_due_cards(tick_at(bot, friday_morning + timedelta(minutes=15)))

    assert len(cards_sent(bot)) == 1
    assert (await card_ids(bot))[session_id] == bot.telegram.last("sendMessage").message[
        "message_id"
    ]


async def test_missed_day_is_caught_up(bot: BotHarness) -> None:
    await make_session(bot, starts_at=SESSION_START)

    await post_due_cards(tick_at(bot, at_sgt(SUNDAY - timedelta(days=1), 10)))

    assert len(cards_sent(bot)) == 1


@pytest.mark.parametrize("status", ["cancelled"])
async def test_cancelled_sessions_get_no_card(bot: BotHarness, status: str) -> None:
    await make_session(bot, starts_at=SESSION_START)
    async with bot.sessionmaker() as db:
        await db.execute(update(Session).values(status=status))
        await db.commit()

    await post_due_cards(tick_at(bot, at_sgt(SUNDAY, 9)))

    assert cards_sent(bot) == []


async def test_ended_sessions_get_no_card(bot: BotHarness) -> None:
    await make_session(bot, starts_at=SESSION_START)

    await post_due_cards(tick_at(bot, SESSION_START + timedelta(hours=2)))

    assert cards_sent(bot) == []


async def test_nothing_is_posted_before_setup(bot: BotHarness) -> None:
    await make_session(bot, starts_at=SESSION_START)
    unconfigured = dataclasses.replace(TEST_SETTINGS, group_chat_id=None)

    await post_due_cards(tick_at(bot, at_sgt(SUNDAY, 9), unconfigured))

    assert cards_sent(bot) == []


async def test_failed_send_is_retried_next_tick(bot: BotHarness) -> None:
    session_id = await make_session(bot, starts_at=SESSION_START)
    bot.telegram.fail("sendMessage", 500, "Internal Server Error")
    friday = at_sgt(SUNDAY - timedelta(days=2), 10)

    with pytest.raises(Exception, match="Internal Server Error"):
        await post_due_cards(tick_at(bot, friday))
    assert (await card_ids(bot))[session_id] is None

    await post_due_cards(tick_at(bot, friday + timedelta(minutes=15)))
    assert len(cards_sent(bot)) == 1


async def test_one_failing_card_does_not_hold_up_the_rest(bot: BotHarness) -> None:
    first = await make_session(bot, starts_at=SESSION_START)
    second = await make_session(bot, starts_at=SESSION_START + timedelta(hours=3))
    bot.telegram.fail("sendMessage", 400, "Bad Request: message is too long")

    with pytest.raises(BadRequest):
        await post_due_cards(tick_at(bot, at_sgt(SUNDAY - timedelta(days=2), 10)))

    ids = await card_ids(bot)
    assert ids[first] is None
    assert ids[second] is not None


async def test_booking_inside_the_window_posts_its_card_at_once(bot: BotHarness) -> None:
    wizard = await book(bot, BOB, future_day(1), hour="20:00")
    await wizard.tap("✅ Confirm")

    assert len(cards_sent(bot)) == 1
    record = bot.telegram.last("sendPhoto")
    card = bot.telegram.last("sendMessage")
    assert bot.telegram.calls.index(record) < bot.telegram.calls.index(card)


async def test_booking_outside_the_window_waits(bot: BotHarness) -> None:
    wizard = await book(bot, BOB, future_day(10))
    await wizard.tap("✅ Confirm")

    assert cards_sent(bot) == []  # the tick posts it two days before
