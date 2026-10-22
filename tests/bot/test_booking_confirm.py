from datetime import timedelta

from sqlalchemy import func, select

from badminton.db.models import Booking, Draft, Member, Session
from badminton.domain.timeutil import at_sgt, fmt_date
from tests.bot.booking_helpers import SHAWN, ZY, book, future_day
from tests.conftest import BotHarness
from tests.fake_telegram import message, message_update


async def stored(bot: BotHarness) -> tuple[list[Session], list[Booking]]:
    async with bot.sessionmaker() as db:
        sessions = list(await db.scalars(select(Session)))
        bookings = list(await db.scalars(select(Booking)))
    return sessions, bookings


async def test_confirm_stores_session_and_booking(bot: BotHarness) -> None:
    day = future_day(14)
    wizard = await book(bot, SHAWN, day)
    assert "How much did you pay?" in wizard.text

    await wizard.tap("$14.80")  # default $7.40/h × 2h
    assert "💵 $14.80, paid by @shawn_t" in wizard.text
    await wizard.tap("✅ Confirm")

    (session,), (booking,) = await stored(bot)
    start = at_sgt(day, 11)
    assert (session.starts_at, session.ends_at, session.status) == (
        start,
        start + timedelta(hours=2),
        "scheduled",
    )
    assert (booking.minutes, booking.amount_cents, booking.photo_file_id) == (
        120,
        1480,
        "photo-1280",
    )
    assert booking.record_message_id == wizard.card["message_id"]
    async with bot.sessionmaker() as db:
        payer = await db.get(Member, booking.payer_member_id)
        assert payer.telegram_user_id == SHAWN["id"]
        assert booking.submitted_by_member_id == payer.id
        assert await db.scalar(select(func.count()).select_from(Draft)) == 0

    due = fmt_date(day - timedelta(days=2))
    assert wizard.text == (
        "✅ <b>Court booked</b> by @shawn_t\n"
        f"📅 {fmt_date(day)}, 11:00–13:00 (2h) · $14.80\n"
        f"🗳 Attendance card goes up on {due}."
    )
    assert wizard.buttons() == {"🗑 Cancel booking": f"bkc:{booking.id}"}


async def test_suggested_price_follows_the_last_booking(bot: BotHarness) -> None:
    first = await book(bot, SHAWN, future_day(14))
    await first.tap("Other…")
    prompt = bot.telegram.last("sendMessage").message
    await bot.send(message_update(message(sender=SHAWN, text="16", reply_to=prompt)))
    await first.tap("✅ Confirm")

    second = await book(bot, ZY, future_day(21), hours="1h")

    assert "$8.00" in second.buttons()


async def test_other_price_is_validated(bot: BotHarness) -> None:
    wizard = await book(bot, SHAWN, future_day(14))
    await wizard.tap("Other…")
    prompt = bot.telegram.last("sendMessage")
    assert "how much did you pay" in prompt.params["text"]

    await bot.send(message_update(message(sender=SHAWN, text="abc", reply_to=prompt.message)))
    reprompt = bot.telegram.last("sendMessage")
    assert reprompt.params["text"].startswith("⚠️ Enter an amount like 14.80")
    await bot.send(message_update(message(sender=SHAWN, text="15.5", reply_to=reprompt.message)))

    assert "💵 $15.50" in wizard.text


async def test_change_price_and_cancel_store_nothing(bot: BotHarness) -> None:
    wizard = await book(bot, SHAWN, future_day(14))
    await wizard.tap("$14.80")

    await wizard.tap("✏️ Change price")
    assert "How long" in wizard.text
    await wizard.tap("1h")
    await wizard.tap("$7.40")
    await wizard.tap("✖ Cancel")

    assert await stored(bot) == ([], [])


async def test_booking_inside_the_reminder_window_says_card_is_going_up(bot: BotHarness) -> None:
    wizard = await book(bot, SHAWN, future_day(1), hour="20:00")
    await wizard.tap("$14.80")
    await wizard.tap("✅ Confirm")

    assert wizard.text.endswith("🗳 The attendance card is going up now.")
