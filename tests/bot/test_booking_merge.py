from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from badminton.db.models import Session
from badminton.domain.timeutil import at_sgt, fmt_date
from tests.bot.booking_helpers import ALICE, BOB, book, confirmed, future_day
from tests.conftest import BotHarness
from tests.fake_telegram import message, message_update

DAY = future_day(14)


async def sessions(bot: BotHarness) -> list[Session]:
    async with bot.sessionmaker() as db:
        found = await db.scalars(
            select(Session).options(selectinload(Session.bookings)).order_by(Session.starts_at)
        )
        return list(found)


async def test_back_to_back_booking_can_join_the_session(bot: BotHarness) -> None:
    await confirmed(bot, ALICE, DAY, "11:00")

    wizard = await book(bot, BOB, DAY, "13:00", "1h")
    assert f"There's already a session on {fmt_date(DAY)}, 11:00–13:00" in wizard.text
    await wizard.tap("➕ Add to 11:00–13:00")
    assert "➕ Part of the 11:00–13:00 session" in wizard.text
    await wizard.tap("✅ Confirm")

    (session,) = await sessions(bot)
    assert (session.starts_at, session.ends_at) == (at_sgt(DAY, 11), at_sgt(DAY, 14))
    assert sorted(b.amount_cents for b in session.bookings) == [740, 1480]
    assert f"➕ Added to the {fmt_date(DAY)}, 11:00–14:00 session" in wizard.record().text


async def test_overlapping_booking_is_offered_too(bot: BotHarness) -> None:
    await confirmed(bot, ALICE, DAY, "11:00")

    wizard = await book(bot, BOB, DAY, "12:00")

    assert "➕ Add to 11:00–13:00" in wizard.buttons()


async def test_separate_session_keeps_them_apart(bot: BotHarness) -> None:
    await confirmed(bot, ALICE, DAY, "11:00")

    wizard = await book(bot, BOB, DAY, "13:00")
    await wizard.tap("Separate session")
    assert "Part of the" not in wizard.text
    await wizard.tap("✅ Confirm")

    assert [(s.starts_at, s.ends_at) for s in await sessions(bot)] == [
        (at_sgt(DAY, 11), at_sgt(DAY, 13)),
        (at_sgt(DAY, 13), at_sgt(DAY, 15)),
    ]


async def test_gap_or_other_day_is_not_offered(bot: BotHarness) -> None:
    await confirmed(bot, ALICE, DAY, "11:00")

    later_same_day = await book(bot, BOB, DAY, "14:00")
    assert "Is this right?" in later_same_day.text
    await later_same_day.tap("✖ Cancel")

    next_day = await book(bot, BOB, future_day(15), "11:00")
    assert "Is this right?" in next_day.text


async def test_cancelled_sessions_are_not_offered(bot: BotHarness) -> None:
    await confirmed(bot, ALICE, DAY, "11:00")
    async with bot.sessionmaker() as db:
        await db.execute(update(Session).values(status="cancelled"))
        await db.commit()

    wizard = await book(bot, BOB, DAY, "13:00")

    assert "Is this right?" in wizard.text


async def test_changing_the_time_asks_again(bot: BotHarness) -> None:
    await confirmed(bot, ALICE, DAY, "11:00")
    wizard = await book(bot, BOB, DAY, "13:00")
    await wizard.tap("➕ Add to 11:00–13:00")

    await wizard.tap("✏️ Edit")
    await wizard.tap("🕐 Time")
    await wizard.tap("09:00")

    assert "There's already a session" in wizard.text
    assert "➕ Add to 11:00–13:00" in wizard.buttons()


async def test_changing_the_price_keeps_the_choice(bot: BotHarness) -> None:
    await confirmed(bot, ALICE, DAY, "11:00")
    wizard = await book(bot, BOB, DAY, "13:00")
    await wizard.tap("Separate session")

    await wizard.tap("✏️ Edit")
    await wizard.tap("💵 Price")
    prompt = bot.telegram.last("sendMessage").message
    await bot.send(message_update(message(sender=BOB, text="20", reply_to=prompt)))

    assert "💵 $20.00" in wizard.text
    assert wizard.text.endswith("Is this right?")
