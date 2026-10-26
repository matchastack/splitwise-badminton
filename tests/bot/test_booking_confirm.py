import dataclasses
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import SAWarning

from badminton.bot.context import SETTINGS_KEY
from badminton.bot.handlers.booking import EXPIRED, NO_GROUP, UNREAD
from badminton.bot.membership import OPEN_CHAT
from badminton.db.models import Booking, Draft, Member, Session
from badminton.domain.timeutil import at_sgt, fmt_date
from tests.bot.booking_helpers import (
    ALICE,
    BOB,
    Wizard,
    activesg_text,
    book,
    future_day,
    record_buttons,
)
from tests.conftest import BotHarness
from tests.fake_telegram import GROUP_ID, callback_update, message, message_update


async def stored(bot: BotHarness) -> tuple[list[Session], list[Booking]]:
    async with bot.sessionmaker() as db:
        sessions = list(await db.scalars(select(Session)))
        bookings = list(await db.scalars(select(Booking)))
    return sessions, bookings


async def test_confirm_posts_the_screenshot_in_the_group_and_pins_it(bot: BotHarness) -> None:
    day = future_day(14)
    wizard = await book(bot, BOB, day)

    await wizard.tap("✅ Confirm")

    (session,), (booking,) = await stored(bot)
    start = at_sgt(day, 11)
    assert (session.starts_at, session.ends_at, session.status) == (
        start,
        start + timedelta(hours=2),
        "scheduled",
    )
    photo_id = wizard.photo["photo"][-1]["file_id"]
    assert (booking.minutes, booking.amount_cents, booking.photo_file_id) == (120, 1480, photo_id)
    assert (booking.venue, booking.courts) == ("Hougang Sport Hall", "11")
    async with bot.sessionmaker() as db:
        payer = await db.get(Member, booking.payer_member_id)
        assert payer.telegram_user_id == BOB["id"]
        assert booking.submitted_by_member_id == payer.id
        assert await db.scalar(select(func.count()).select_from(Draft)) == 0

    posted = bot.telegram.last("sendPhoto").params
    assert (posted["chat_id"], posted["photo"]) == (GROUP_ID, photo_id)
    record = wizard.record()
    assert booking.record_message_id == record.card["message_id"]
    due = fmt_date(day - timedelta(days=2))
    assert record.text == (
        f"📅 {fmt_date(day)}, 11:00–13:00 (2h)\n"
        "📍 Hougang Sport Hall · Court 11\n"
        "💵 $14.80, paid by @bob_t\n"
        f"🗳 Attendance card goes up on {due}."
    )
    # Telegram shows a pinned message's button in the pin bar when it's the only one.
    assert record.current["reply_markup"]["inline_keyboard"] == [
        [
            {"text": OPEN_CHAT, "url": "https://t.me/badminton_bot"},
            {"text": "🗑 Cancel booking", "callback_data": f"bkc:{booking.id}"},
        ]
    ]

    pinned = bot.telegram.last("pinChatMessage").params
    assert pinned == {
        "chat_id": GROUP_ID,
        "message_id": record.card["message_id"],
        "disable_notification": True,
    }
    # In the private chat, the card says so in a line, and the member's screenshot stays.
    assert wizard.card["chat"]["id"] == BOB["id"]
    assert (
        wizard.text == f"✅ Booked {fmt_date(day)}, 11:00–13:00 (2h), and posted it in the group."
    )
    assert wizard.buttons() == {}
    deleted = [call.params["message_id"] for call in bot.telegram.calls_to("deleteMessage")]
    assert deleted == [wizard.prompt["message_id"]]  # only the answered prompt


async def test_a_booking_without_venue_or_court_is_recorded_without_them(bot: BotHarness) -> None:
    wizard = await Wizard(bot, BOB).start()
    await wizard.pick_date(future_day(14))
    await wizard.tap("11:00")
    await wizard.tap("1h")

    await wizard.tap("✅ Confirm")

    (booking,) = (await stored(bot))[1]
    assert (booking.venue, booking.courts) == (None, None)
    assert "📍" not in wizard.record().text


async def test_a_pin_the_bot_may_not_make_still_records_the_booking(bot: BotHarness) -> None:
    wizard = await book(bot, BOB, future_day(14))
    bot.telegram.fail("pinChatMessage", 400, "Bad Request: not enough rights to pin a message")

    await wizard.tap("✅ Confirm")

    (booking,) = (await stored(bot))[1]
    assert booking.record_message_id == wizard.record().card["message_id"]
    assert wizard.text.startswith("✅ Booked ")


async def test_a_second_confirm_tap_keeps_the_record(bot: BotHarness) -> None:
    """The second tap of a double-tap waits for the first, then finds the form gone."""
    wizard = await book(bot, BOB, future_day(14))
    confirm = callback_update(wizard.buttons()["✅ Confirm"], sender=BOB, on_message=wizard.card)
    await bot.send(confirm)
    calls = len(bot.telegram.calls)

    await bot.send(confirm)

    _, (booking,) = await stored(bot)
    assert wizard.last_alert() == EXPIRED
    assert [call.method for call in bot.telegram.calls[calls:]] == ["answerCallbackQuery"]
    assert wizard.record().buttons() == record_buttons(booking.id)


async def test_a_double_tap_on_confirm_books_once(
    bot: BotHarness, recwarn: pytest.WarningsRecorder
) -> None:
    wizard = await book(bot, BOB, future_day(14))

    await bot.double_tap(
        callback_update(wizard.buttons()["✅ Confirm"], sender=BOB, on_message=wizard.card)
    )

    (_,), (booking,) = await stored(bot)
    answers = [call.params.get("text") for call in bot.telegram.calls_to("answerCallbackQuery")]
    assert sorted(answers[-2:], key=str) == [None, EXPIRED]  # one went through
    assert len(bot.telegram.calls_to("sendPhoto")) == 1  # one record, pinned once
    assert len(bot.telegram.calls_to("pinChatMessage")) == 1
    assert wizard.record().buttons() == record_buttons(booking.id)
    assert not [w for w in recwarn if issubclass(w.category, SAWarning)]


async def test_the_suggestion_is_the_court_rate_whatever_the_last_price(bot: BotHarness) -> None:
    """An off-peak or bought-through-someone booking is priced with ✏️ Edit, and doesn't
    change what the next booking suggests."""
    first = await book(bot, BOB, future_day(14))
    await first.tap("✏️ Edit")
    await first.tap("💵 Price")
    prompt = bot.telegram.last("sendMessage").message
    await bot.send(message_update(message(sender=BOB, text="6", reply_to=prompt)))
    await first.tap("✅ Confirm")

    second = await book(bot, ALICE, future_day(21), hours="1h")

    assert "💵 $7.40, paid by @alice_handle" in second.text


async def test_the_court_rate_is_the_setting(bot: BotHarness) -> None:
    rate = dataclasses.replace(bot.settings, default_court_rate_cents=800)
    bot.application.bot_data[SETTINGS_KEY] = rate

    wizard = await book(bot, BOB, future_day(14))

    assert "💵 $16.00, paid by @bob_t" in wizard.text  # 2h × $8.00


async def test_two_courts_at_once_are_priced_as_two(bot: BotHarness) -> None:
    day = future_day(14)
    screenshot = activesg_text(day, hours=1, courts=("11", "12"))

    wizard = await Wizard(bot, BOB, screenshot).start()

    assert wizard.text.startswith(
        f"📅 {fmt_date(day)}, 11:00–12:00 (1h)\n"
        "📍 Hougang Sport Hall · Courts 11, 12\n"
        "💵 $14.80, paid by @bob_t"
    )


async def test_a_screenshot_of_a_past_day_asks_for_the_date(bot: BotHarness) -> None:
    wizard = await book(bot, BOB, future_day(-7))

    assert (
        wizard.text
        == f"{UNREAD}\n📍 Hougang Sport Hall · Court 11\nWhich day is the court booked for?"
    )


async def test_a_slot_earlier_today_cannot_be_confirmed(bot: BotHarness) -> None:
    wizard = await Wizard(bot, BOB, activesg_text(future_day(0), hour=0, hours=1)).start()
    assert wizard.text.endswith("⚠️ That time has passed.\nIs this right?")

    await wizard.tap("✅ Confirm")

    assert wizard.last_alert() == "That time has passed. Change the date or time first."
    assert await stored(bot) == ([], [])


async def test_booking_inside_the_reminder_window_says_card_is_going_up(bot: BotHarness) -> None:
    wizard = await book(bot, BOB, future_day(1), hour="20:00")
    await wizard.tap("✅ Confirm")

    assert wizard.record().text.endswith("🗳 The attendance card is going up now.")


async def test_cancel_stores_nothing(bot: BotHarness) -> None:
    wizard = await book(bot, BOB, future_day(14))
    await wizard.tap("✏️ Edit")
    await wizard.tap("⏱ Hours")
    await wizard.tap("1h")

    await wizard.tap("✖ Cancel")

    assert await stored(bot) == ([], [])
    assert bot.telegram.calls_to("sendPhoto") == []


async def test_a_session_that_was_cancelled_meanwhile_is_not_joined(bot: BotHarness) -> None:
    day = future_day(14)
    first = await book(bot, ALICE, day)
    await first.tap("✅ Confirm")
    wizard = await book(bot, BOB, day, hour="13:00", hours="1h")
    await wizard.tap("➕ Add to 11:00–13:00")
    async with bot.sessionmaker() as db:
        await db.execute(update(Session).values(status="cancelled"))
        await db.commit()

    await wizard.tap("✅ Confirm")

    sessions, _ = await stored(bot)
    assert sorted(s.status for s in sessions) == ["cancelled", "scheduled"]


async def test_a_network_error_pinning_still_finishes_the_booking(bot: BotHarness) -> None:
    wizard = await book(bot, BOB, future_day(1), hour="20:00")  # its card is due at once
    bot.telegram.fail("pinChatMessage", 502, "Bad Gateway")

    await wizard.tap("✅ Confirm")

    assert len((await stored(bot))[1]) == 1
    assert wizard.text.startswith("✅ Booked ")
    assert any("No answer = not playing." in text for text in bot.telegram.sent_texts(GROUP_ID))


async def test_a_booking_needs_the_group_to_be_set(bot: BotHarness) -> None:
    """Before setup, only the admin can use the bot privately (FR-MEM-9)."""
    wizard = await book(bot, ALICE, future_day(14))
    unconfigured = dataclasses.replace(bot.settings, group_chat_id=None)
    bot.application.bot_data[SETTINGS_KEY] = unconfigured

    await wizard.tap("✅ Confirm")

    assert wizard.last_alert() == NO_GROUP
    assert await stored(bot) == ([], [])
    assert bot.telegram.calls_to("sendPhoto") == []


async def test_a_session_booked_meanwhile_is_offered_before_confirming(bot: BotHarness) -> None:
    day = future_day(14)
    wizard = await book(bot, BOB, day, hour="13:00")
    assert wizard.text.endswith("Is this right?")  # nothing to join yet
    other = await book(bot, ALICE, day, hour="11:00")
    await other.tap("✅ Confirm")

    await wizard.tap("✅ Confirm")

    assert wizard.last_alert() == "There's now a session next to this one: is it part of it?"
    assert "➕ Add to 11:00–13:00" in wizard.buttons()
    assert len((await stored(bot))[1]) == 1  # only alice's
