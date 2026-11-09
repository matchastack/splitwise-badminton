import dataclasses
from datetime import date, timedelta

import pytest
from sqlalchemy import select, update
from telegram.error import BadRequest

from badminton.db.models import Booking, Session
from badminton.domain.timeutil import at_sgt, fmt_date, fmt_session, utc_now
from badminton.services.attendance import IN, post_missing_cards, send_due_reminders, set_attendance
from badminton.services.schedule import default_steps
from badminton.services.tick import TickContext
from tests.bot.attendance_helpers import make_session
from tests.bot.booking_helpers import ALICE, BOB, book, future_day
from tests.conftest import TEST_SETTINGS, BotHarness
from tests.fake_telegram import GROUP_ID

SUNDAY = date(2026, 11, 8)
SESSION_START = at_sgt(SUNDAY, 11)
MONDAY_BEFORE = at_sgt(SUNDAY - timedelta(days=6), 10)
FRIDAY_MORNING = at_sgt(SUNDAY - timedelta(days=2), 10)


def tick_at(bot: BotHarness, when, settings=TEST_SETTINGS) -> TickContext:
    return TickContext(bot.application.bot, settings, bot.sessionmaker, when)


async def tick(bot: BotHarness, when, settings=TEST_SETTINGS) -> None:
    """The tick's attendance steps, in order."""
    await post_missing_cards(tick_at(bot, when, settings))
    await send_due_reminders(tick_at(bot, when, settings))


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


def reminders_sent(bot: BotHarness) -> list[dict]:
    """The parameters of each reminder Telegram accepted."""
    return [
        call.params
        for call in bot.telegram.calls_to("sendMessage")
        if call.result is not None and call.params["text"].startswith("⏰ <b>Reminder:</b>")
    ]


def test_the_tick_posts_cards_before_reminders() -> None:
    names = [name for name, _ in default_steps()]
    assert names.index("attendance_cards") < names.index("attendance_reminders")


# The card ---------------------------------------------------------------------


async def test_booking_posts_its_card_at_once(bot: BotHarness) -> None:
    wizard = await book(bot, BOB, future_day(10))
    await wizard.tap("✅ Confirm")

    assert len(cards_sent(bot)) == 1
    record = bot.telegram.last("sendPhoto")
    card = bot.telegram.last("sendMessage")
    assert bot.telegram.calls.index(record) < bot.telegram.calls.index(card)
    async with bot.sessionmaker() as db:
        (session,) = await db.scalars(select(Session))
        assert session.card_message_id == card.message["message_id"]


async def test_booking_inside_the_reminder_window_posts_its_card_at_once(bot: BotHarness) -> None:
    wizard = await book(bot, BOB, future_day(1), hour="20:00")
    await wizard.tap("✅ Confirm")

    assert len(cards_sent(bot)) == 1


async def test_joining_a_session_updates_its_card_instead_of_posting_another(
    bot: BotHarness,
) -> None:
    day = future_day(10)
    first = await book(bot, ALICE, day)
    await first.tap("✅ Confirm")
    second = await book(bot, BOB, day, hour="13:00", hours="1h")
    await second.tap("➕ Add to 11:00–13:00")

    await second.tap("✅ Confirm")

    assert len(cards_sent(bot)) == 1
    (card_id,) = (await card_ids(bot)).values()
    edits = [
        call.params["text"]
        for call in bot.telegram.calls_to("editMessageText")
        if call.params["message_id"] == card_id
    ]
    assert "11:00–14:00" in edits[-1]


async def test_the_tick_posts_a_missing_card_once(bot: BotHarness) -> None:
    """E.g. a session whose card failed to send when it was booked."""
    session_id = await make_session(bot, starts_at=SESSION_START)

    await post_missing_cards(tick_at(bot, MONDAY_BEFORE))
    await post_missing_cards(tick_at(bot, MONDAY_BEFORE + timedelta(minutes=15)))

    assert len(cards_sent(bot)) == 1
    assert (await card_ids(bot))[session_id] == bot.telegram.last("sendMessage").message[
        "message_id"
    ]


@pytest.mark.parametrize("status", ["cancelled"])
async def test_cancelled_sessions_get_no_card(bot: BotHarness, status: str) -> None:
    await make_session(bot, starts_at=SESSION_START)
    async with bot.sessionmaker() as db:
        await db.execute(update(Session).values(status=status))
        await db.commit()

    await post_missing_cards(tick_at(bot, at_sgt(SUNDAY, 9)))

    assert cards_sent(bot) == []


async def test_ended_sessions_get_no_card(bot: BotHarness) -> None:
    await make_session(bot, starts_at=SESSION_START)

    await post_missing_cards(tick_at(bot, SESSION_START + timedelta(hours=2)))

    assert cards_sent(bot) == []


async def test_nothing_is_posted_before_setup(bot: BotHarness) -> None:
    await make_session(bot, starts_at=SESSION_START)
    unconfigured = dataclasses.replace(TEST_SETTINGS, group_chat_id=None)

    await tick(bot, at_sgt(SUNDAY, 9), unconfigured)

    assert bot.telegram.calls_to("sendMessage") == []


async def test_failed_send_is_retried_next_tick(bot: BotHarness) -> None:
    session_id = await make_session(bot, starts_at=SESSION_START)
    bot.telegram.fail("sendMessage", 500, "Internal Server Error")

    with pytest.raises(Exception, match="Internal Server Error"):
        await post_missing_cards(tick_at(bot, MONDAY_BEFORE))
    assert (await card_ids(bot))[session_id] is None

    await post_missing_cards(tick_at(bot, MONDAY_BEFORE + timedelta(minutes=15)))
    assert len(cards_sent(bot)) == 1


async def test_one_failing_card_does_not_hold_up_the_rest(bot: BotHarness) -> None:
    first = await make_session(bot, starts_at=SESSION_START)
    second = await make_session(bot, starts_at=SESSION_START + timedelta(hours=3))
    bot.telegram.fail("sendMessage", 400, "Bad Request: message is too long")

    with pytest.raises(BadRequest):
        await post_missing_cards(tick_at(bot, MONDAY_BEFORE))

    ids = await card_ids(bot)
    assert ids[first] is None
    assert ids[second] is not None


# The reminder -----------------------------------------------------------------


async def test_reminder_is_sent_two_days_before_and_only_once(bot: BotHarness) -> None:
    session_id = await make_session(bot, starts_at=SESSION_START)
    await tick(bot, MONDAY_BEFORE)

    await tick(bot, at_sgt(SUNDAY - timedelta(days=3), 23, 59))
    assert reminders_sent(bot) == []

    await tick(bot, FRIDAY_MORNING)
    await tick(bot, FRIDAY_MORNING + timedelta(minutes=15))

    (reminder,) = reminders_sent(bot)
    card_id = (await card_ids(bot))[session_id]
    assert reminder["chat_id"] == GROUP_ID
    reply_to = reminder["reply_parameters"]
    assert (reply_to["message_id"], reply_to["allow_sending_without_reply"]) == (card_id, True)
    assert reminder["text"] == (
        "⏰ <b>Reminder:</b> badminton in 2 days, "
        f"{fmt_session(SESSION_START, SESSION_START + timedelta(hours=2))}.\n"
        "✅ In so far: 0. Answer on the card if you haven't: no answer = not playing."
    )
    assert "@" not in reminder["text"]  # nobody is pinged (FR-ATT-4)
    assert reminder.get("reply_markup") is None
    assert len(cards_sent(bot)) == 1


async def test_reminder_counts_who_is_in(bot: BotHarness) -> None:
    session_id = await make_session(bot, starts_at=SESSION_START)
    await tick(bot, MONDAY_BEFORE)
    async with bot.sessionmaker() as db:
        payer_id = await db.scalar(
            select(Booking.payer_member_id).where(Booking.session_id == session_id)
        )
        await set_attendance(db, session_id, payer_id, IN)
        await db.commit()

    await tick(bot, FRIDAY_MORNING)

    (reminder,) = reminders_sent(bot)
    assert "✅ In so far: 1." in reminder["text"]


async def test_missed_day_is_caught_up(bot: BotHarness) -> None:
    await make_session(bot, starts_at=SESSION_START)
    await tick(bot, MONDAY_BEFORE)

    await tick(bot, at_sgt(SUNDAY - timedelta(days=1), 10))

    (reminder,) = reminders_sent(bot)
    assert reminder["text"].startswith("⏰ <b>Reminder:</b> badminton tomorrow, ")


async def test_a_session_that_has_started_gets_no_reminder(bot: BotHarness) -> None:
    await make_session(bot, starts_at=SESSION_START)
    await tick(bot, MONDAY_BEFORE)

    await tick(bot, SESSION_START + timedelta(minutes=30))

    assert reminders_sent(bot) == []


async def test_a_card_that_went_up_inside_the_window_gets_no_reminder(bot: BotHarness) -> None:
    """Its card has only just gone up, e.g. a session booked at short notice."""
    await make_session(bot, starts_at=SESSION_START)

    await tick(bot, FRIDAY_MORNING)
    await tick(bot, at_sgt(SUNDAY, 9))

    assert len(cards_sent(bot)) == 1
    assert reminders_sent(bot) == []


async def test_a_session_booked_ahead_gets_its_reminder(bot: BotHarness) -> None:
    day = future_day(10)
    wizard = await book(bot, BOB, day)
    await wizard.tap("✅ Confirm")
    assert wizard.record().text.endswith(
        f"⏰ Attendance reminder on {fmt_date(day - timedelta(days=2))}."
    )

    await tick(bot, at_sgt(day - timedelta(days=3), 10))
    assert reminders_sent(bot) == []
    await tick(bot, at_sgt(day - timedelta(days=2), 10))

    assert len(reminders_sent(bot)) == 1


async def test_a_session_booked_inside_the_window_gets_no_reminder(bot: BotHarness) -> None:
    wizard = await book(bot, BOB, future_day(1), hour="20:00")
    await wizard.tap("✅ Confirm")

    await tick(bot, utc_now() + timedelta(hours=1))

    assert reminders_sent(bot) == []


async def test_cancelled_sessions_get_no_reminder(bot: BotHarness) -> None:
    await make_session(bot, starts_at=SESSION_START)
    await tick(bot, MONDAY_BEFORE)
    async with bot.sessionmaker() as db:
        await db.execute(update(Session).values(status="cancelled"))
        await db.commit()

    await tick(bot, FRIDAY_MORNING)

    assert reminders_sent(bot) == []


async def test_failed_reminder_is_retried_next_tick(bot: BotHarness) -> None:
    await make_session(bot, starts_at=SESSION_START)
    await tick(bot, MONDAY_BEFORE)
    bot.telegram.fail("sendMessage", 500, "Internal Server Error")

    with pytest.raises(Exception, match="Internal Server Error"):
        await send_due_reminders(tick_at(bot, FRIDAY_MORNING))
    assert reminders_sent(bot) == []

    await send_due_reminders(tick_at(bot, FRIDAY_MORNING + timedelta(minutes=15)))
    assert len(reminders_sent(bot)) == 1


async def test_one_failing_reminder_does_not_hold_up_the_rest(bot: BotHarness) -> None:
    await make_session(bot, starts_at=SESSION_START)
    await make_session(bot, starts_at=SESSION_START + timedelta(hours=3))
    await tick(bot, MONDAY_BEFORE)
    bot.telegram.fail("sendMessage", 400, "Bad Request: message is too long")

    with pytest.raises(BadRequest):
        await send_due_reminders(tick_at(bot, FRIDAY_MORNING))

    assert len(reminders_sent(bot)) == 1
    async with bot.sessionmaker() as db:
        sent = [s.reminder_sent_at for s in await db.scalars(select(Session).order_by(Session.id))]
    assert sent[0] is None
    assert sent[1] is not None


async def test_a_card_left_in_another_chat_gets_a_reminder_without_a_reply(
    bot: BotHarness,
) -> None:
    """After the bot moves to another group, the reminder goes to the new one."""
    await make_session(bot, starts_at=SESSION_START)
    await tick(bot, MONDAY_BEFORE)
    async with bot.sessionmaker() as db:
        await db.execute(update(Session).values(card_chat_id=-100999))
        await db.commit()

    await tick(bot, FRIDAY_MORNING)

    (reminder,) = reminders_sent(bot)
    assert reminder["chat_id"] == GROUP_ID
    assert "reply_parameters" not in reminder
