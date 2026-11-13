import dataclasses
from datetime import date, timedelta

import pytest
from sqlalchemy import select, update
from telegram.error import BadRequest

from badminton.db.models import Booking, Session
from badminton.domain.timeutil import at_sgt
from badminton.services.attendance import post_missing_cards
from badminton.services.tick import TickContext
from tests.bot.attendance_helpers import make_session
from tests.bot.booking_helpers import ALICE, BOB, book, confirmed, future_day
from tests.conftest import TEST_SETTINGS, BotHarness
from tests.fake_telegram import GROUP_ID, ApiCall

SUNDAY = date(2026, 11, 8)
SESSION_START = at_sgt(SUNDAY, 11)
FRIDAY_TEN = at_sgt(SUNDAY - timedelta(days=2), 10)


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


def replies_to(call: ApiCall) -> dict | None:
    """The reply a sent message asked for, as Telegram receives it."""
    return call.params.get("reply_parameters")


async def forget_card(bot: BotHarness) -> None:
    """Leave every session without a card, as if posting it had failed or it was booked
    before cards went up on confirming."""
    async with bot.sessionmaker() as db:
        await db.execute(update(Session).values(card_sent_at=None, card_message_id=None))
        await db.commit()
    bot.telegram.clear()


# Confirming a booking posts the card -----------------------------------------------


@pytest.mark.parametrize("days_ahead", [1, 14])
async def test_confirming_a_booking_posts_its_card_in_reply_to_the_record(
    bot: BotHarness, days_ahead: int
) -> None:
    record = await confirmed(bot, BOB, future_day(days_ahead), "20:00")

    (card,) = card_calls(bot)
    assert bot.telegram.calls.index(card) > bot.telegram.calls.index(bot.telegram.last("sendPhoto"))
    assert replies_to(card)["message_id"] == record.card["message_id"]
    assert replies_to(card)["allow_sending_without_reply"]  # still posted if it was deleted
    assert list((await card_ids(bot)).values()) == [card.message["message_id"]]


async def test_joining_a_session_updates_its_card_instead_of_posting_another(
    bot: BotHarness,
) -> None:
    day = future_day(14)
    first = await confirmed(bot, ALICE, day, "11:00")
    (card,) = card_calls(bot)

    joiner = await book(bot, BOB, day, "13:00", "1h")
    await joiner.tap("➕ Add to 11:00–13:00")
    await joiner.tap("✅ Confirm")

    assert card_calls(bot) == [card]
    assert replies_to(card)["message_id"] == first.card["message_id"]
    edits = bot.telegram.calls_to("editMessageText")
    (edited,) = [e for e in edits if e.params["message_id"] == card.message["message_id"]]
    assert "\n💵 Court $22.20\n" in edited.params["text"]


async def test_a_card_telegram_refuses_on_confirming_is_posted_by_the_tick(
    bot: BotHarness,
) -> None:
    day = future_day(14)
    wizard = await book(bot, BOB, day)
    bot.telegram.fail("sendMessage", 502, "Bad Gateway", chat_id=GROUP_ID)

    await wizard.tap("✅ Confirm")  # the booking still goes through

    assert wizard.text.startswith("✅ Booked ")
    assert len(bot.telegram.calls_to("pinChatMessage")) == 1
    assert card_calls(bot) == []
    assert list((await card_ids(bot)).values()) == [None]

    await post_missing_cards(tick_at(bot, at_sgt(future_day(1), 10)))

    (card,) = card_calls(bot)
    assert replies_to(card)["message_id"] == wizard.record().card["message_id"]


# The tick catches up on missing cards ----------------------------------------------


async def test_a_missing_card_is_posted_once(bot: BotHarness) -> None:
    session_id = await make_session(bot, starts_at=SESSION_START)

    await post_missing_cards(tick_at(bot, FRIDAY_TEN - timedelta(days=5)))
    await post_missing_cards(tick_at(bot, FRIDAY_TEN))

    (card,) = card_calls(bot)
    assert (await card_ids(bot))[session_id] == card.message["message_id"]


async def test_missing_cards_wait_for_ten_sgt(bot: BotHarness) -> None:
    await make_session(bot, starts_at=SESSION_START)
    friday = SUNDAY - timedelta(days=2)

    await post_missing_cards(tick_at(bot, at_sgt(friday, 0)))
    await post_missing_cards(tick_at(bot, at_sgt(friday, 9, 45)))
    assert card_calls(bot) == []

    await post_missing_cards(tick_at(bot, at_sgt(friday, 10)))
    assert len(card_calls(bot)) == 1


async def test_cancelled_sessions_get_no_card(bot: BotHarness) -> None:
    await make_session(bot, starts_at=SESSION_START)
    async with bot.sessionmaker() as db:
        await db.execute(update(Session).values(status="cancelled"))
        await db.commit()

    await post_missing_cards(tick_at(bot, FRIDAY_TEN))

    assert card_calls(bot) == []


async def test_ended_sessions_get_no_card(bot: BotHarness) -> None:
    await make_session(bot, starts_at=SESSION_START)

    await post_missing_cards(tick_at(bot, SESSION_START + timedelta(hours=2)))

    assert card_calls(bot) == []


async def test_nothing_is_posted_before_setup(bot: BotHarness) -> None:
    await make_session(bot, starts_at=SESSION_START)
    unconfigured = dataclasses.replace(TEST_SETTINGS, group_chat_id=None)

    await post_missing_cards(tick_at(bot, FRIDAY_TEN, unconfigured))

    assert card_calls(bot) == []


async def test_failed_send_is_retried_next_tick(bot: BotHarness) -> None:
    session_id = await make_session(bot, starts_at=SESSION_START)
    bot.telegram.fail("sendMessage", 500, "Internal Server Error")

    with pytest.raises(Exception, match="Internal Server Error"):
        await post_missing_cards(tick_at(bot, FRIDAY_TEN))
    assert (await card_ids(bot))[session_id] is None

    await post_missing_cards(tick_at(bot, FRIDAY_TEN + timedelta(minutes=15)))
    assert len(card_calls(bot)) == 1


async def test_one_failing_card_does_not_hold_up_the_rest(bot: BotHarness) -> None:
    first = await make_session(bot, starts_at=SESSION_START)
    second = await make_session(bot, starts_at=SESSION_START + timedelta(hours=3))
    bot.telegram.fail("sendMessage", 400, "Bad Request: message is too long")

    with pytest.raises(BadRequest):
        await post_missing_cards(tick_at(bot, FRIDAY_TEN))

    ids = await card_ids(bot)
    assert ids[first] is None
    assert ids[second] is not None


# Which record a card replies to -----------------------------------------------------


async def test_a_card_posted_later_replies_to_the_first_record_still_standing(
    bot: BotHarness,
) -> None:
    day = future_day(14)
    first = await confirmed(bot, ALICE, day, "11:00")
    joiner = await book(bot, BOB, day, "13:00", "1h")
    await joiner.tap("➕ Add to 11:00–13:00")
    await joiner.tap("✅ Confirm")
    second = joiner.record()

    await forget_card(bot)
    await post_missing_cards(tick_at(bot, at_sgt(future_day(1), 10)))
    (card,) = card_calls(bot)
    assert replies_to(card)["message_id"] == first.card["message_id"]

    await forget_card(bot)
    await first.tap("🗑 Cancel booking")
    await first.tap("Yes, cancel it")
    await post_missing_cards(tick_at(bot, at_sgt(future_day(1), 10)))
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

    await post_missing_cards(tick_at(bot, FRIDAY_TEN))

    assert set(await card_ids(bot)) == {imported, moved}
    assert [replies_to(card) for card in card_calls(bot)] == [None, None]
