import dataclasses
from datetime import date, timedelta

import pytest
from sqlalchemy import select, update
from telegram.error import BadRequest

from badminton.db.models import Booking, Session
from badminton.domain.timeutil import at_sgt, card_due_date
from badminton.services.attendance import post_due_cards
from badminton.services.tick import TickContext
from tests.bot.attendance_helpers import make_session
from tests.bot.booking_helpers import ALICE, BOB, book, confirmed, future_day
from tests.conftest import TEST_SETTINGS, BotHarness
from tests.fake_telegram import GROUP_ID, ApiCall

SUNDAY = date(2026, 11, 8)
SESSION_START = at_sgt(SUNDAY, 11)


def tick_at(bot: BotHarness, when, settings=TEST_SETTINGS) -> TickContext:
    return TickContext(bot.application.bot, settings, bot.sessionmaker, when)


async def card_ids(bot: BotHarness) -> dict[int, int | None]:
    async with bot.sessionmaker() as db:
        return {s.id: s.card_message_id for s in await db.scalars(select(Session))}


def card_calls(bot: BotHarness) -> list[ApiCall]:
    """The attendance cards Telegram accepted."""
    return [
        call
        for call in bot.telegram.calls_to("sendMessage")
        if call.result is not None
        and call.params["chat_id"] == GROUP_ID
        and "No answer = not playing." in call.params["text"]
    ]


def cards_sent(bot: BotHarness) -> list[str]:
    return [call.params["text"] for call in card_calls(bot)]


def replies_to(call: ApiCall) -> dict | None:
    """The reply a sent message asked for, as Telegram receives it."""
    return call.params.get("reply_parameters")


async def tick_on_card_day(bot: BotHarness, day: date) -> None:
    """Run the tick at 10:00 SGT on the day the card for a session on ``day`` is due."""
    due = card_due_date(at_sgt(day, 11), TEST_SETTINGS.reminder_days_before)
    await post_due_cards(tick_at(bot, at_sgt(due, 10)))


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


async def test_cards_wait_for_ten_sgt(bot: BotHarness) -> None:
    await make_session(bot, starts_at=SESSION_START)
    friday = SUNDAY - timedelta(days=2)

    await post_due_cards(tick_at(bot, at_sgt(friday, 0)))
    await post_due_cards(tick_at(bot, at_sgt(friday, 9, 45)))
    assert cards_sent(bot) == []

    await post_due_cards(tick_at(bot, at_sgt(friday, 10)))
    assert len(cards_sent(bot)) == 1


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

    await post_due_cards(tick_at(bot, at_sgt(SUNDAY, 10)))

    assert cards_sent(bot) == []


async def test_ended_sessions_get_no_card(bot: BotHarness) -> None:
    await make_session(bot, starts_at=SESSION_START)

    await post_due_cards(tick_at(bot, SESSION_START + timedelta(hours=2)))

    assert cards_sent(bot) == []


async def test_nothing_is_posted_before_setup(bot: BotHarness) -> None:
    await make_session(bot, starts_at=SESSION_START)
    unconfigured = dataclasses.replace(TEST_SETTINGS, group_chat_id=None)

    await post_due_cards(tick_at(bot, at_sgt(SUNDAY, 10), unconfigured))

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


async def test_the_card_replies_to_the_pinned_booking_record(bot: BotHarness) -> None:
    day = future_day(10)
    record = await confirmed(bot, BOB, day)
    assert cards_sent(bot) == []

    await tick_on_card_day(bot, day)

    (card,) = card_calls(bot)
    assert replies_to(card)["message_id"] == record.card["message_id"]
    assert replies_to(card)["allow_sending_without_reply"]  # still posted if it was deleted


async def test_a_card_posted_on_confirming_replies_to_the_new_record(bot: BotHarness) -> None:
    record = await confirmed(bot, BOB, future_day(1), hour="20:00")

    (card,) = card_calls(bot)
    assert replies_to(card)["message_id"] == record.card["message_id"]


async def test_a_merged_session_replies_to_its_first_record(bot: BotHarness) -> None:
    day = future_day(10)
    first = await confirmed(bot, ALICE, day, "11:00")
    joiner = await book(bot, BOB, day, "13:00", "1h")
    await joiner.tap("➕ Add to 11:00–13:00")
    await joiner.tap("✅ Confirm")

    await tick_on_card_day(bot, day)

    (card,) = card_calls(bot)
    assert replies_to(card)["message_id"] == first.card["message_id"]


async def test_a_cancelled_booking_record_is_skipped(bot: BotHarness) -> None:
    day = future_day(10)
    first = await confirmed(bot, ALICE, day, "11:00")
    joiner = await book(bot, BOB, day, "13:00", "1h")
    await joiner.tap("➕ Add to 11:00–13:00")
    await joiner.tap("✅ Confirm")
    second = joiner.record()
    await first.tap("🗑 Cancel booking")
    await first.tap("Yes, cancel it")

    await tick_on_card_day(bot, day)

    (card,) = card_calls(bot)
    assert replies_to(card)["message_id"] == second.card["message_id"]


async def test_a_session_without_a_record_in_the_group_posts_on_its_own(
    bot: BotHarness,
) -> None:
    imported = await make_session(bot, starts_at=SESSION_START)  # as from the spreadsheet
    moved = await make_session(bot, starts_at=SESSION_START + timedelta(hours=3))
    async with bot.sessionmaker() as db:  # recorded in the group the bot was moved from
        await db.execute(
            update(Booking)
            .where(Booking.session_id == moved)
            .values(record_chat_id=-100999, record_message_id=77)
        )
        await db.commit()

    await post_due_cards(tick_at(bot, at_sgt(SUNDAY - timedelta(days=2), 10)))

    assert set(await card_ids(bot)) == {imported, moved}
    assert [replies_to(card) for card in card_calls(bot)] == [None, None]
