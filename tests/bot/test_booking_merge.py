from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from badminton.db.models import Session
from badminton.domain.timeutil import at_sgt, fmt_date
from tests.bot.booking_helpers import ALICE, BOB, book, future_day
from tests.conftest import BotHarness

DAY = future_day(14)


async def confirmed(bot: BotHarness, sender: dict, hour: str, hours: str = "2h"):
    wizard = await book(bot, sender, DAY, hour=hour, hours=hours)
    await wizard.tap(next(label for label in wizard.buttons() if label.startswith("$")))
    return wizard


async def sessions(bot: BotHarness) -> list[Session]:
    async with bot.sessionmaker() as db:
        found = await db.scalars(
            select(Session).options(selectinload(Session.bookings)).order_by(Session.starts_at)
        )
        return list(found)


async def test_back_to_back_booking_can_join_the_session(bot: BotHarness) -> None:
    await (await confirmed(bot, ALICE, "11:00")).tap("✅ Confirm")

    wizard = await confirmed(bot, BOB, "13:00", "1h")
    assert f"There's already a session on {fmt_date(DAY)}, 11:00–13:00" in wizard.text
    await wizard.tap("➕ Add to 11:00–13:00")
    assert "➕ Part of the 11:00–13:00 session" in wizard.text
    await wizard.tap("✅ Confirm")

    (session,) = await sessions(bot)
    assert (session.starts_at, session.ends_at) == (at_sgt(DAY, 11), at_sgt(DAY, 14))
    assert sorted(b.amount_cents for b in session.bookings) == [740, 1480]
    assert f"➕ Added to the {fmt_date(DAY)}, 11:00–14:00 session" in wizard.text


async def test_overlapping_booking_is_offered_too(bot: BotHarness) -> None:
    await (await confirmed(bot, ALICE, "11:00")).tap("✅ Confirm")

    wizard = await confirmed(bot, BOB, "12:00")

    assert "➕ Add to 11:00–13:00" in wizard.buttons()


async def test_separate_session_keeps_them_apart(bot: BotHarness) -> None:
    await (await confirmed(bot, ALICE, "11:00")).tap("✅ Confirm")

    wizard = await confirmed(bot, BOB, "13:00")
    await wizard.tap("Separate session")
    assert "Part of the" not in wizard.text
    await wizard.tap("✅ Confirm")

    assert [(s.starts_at, s.ends_at) for s in await sessions(bot)] == [
        (at_sgt(DAY, 11), at_sgt(DAY, 13)),
        (at_sgt(DAY, 13), at_sgt(DAY, 15)),
    ]


async def test_gap_or_other_day_is_not_offered(bot: BotHarness) -> None:
    await (await confirmed(bot, ALICE, "11:00")).tap("✅ Confirm")

    later_same_day = await confirmed(bot, BOB, "14:00")
    assert "Is this right?" in later_same_day.text
    await later_same_day.tap("✖ Cancel")

    next_day = await book(bot, BOB, future_day(15), hour="11:00")
    await next_day.tap("$14.80")
    assert "Is this right?" in next_day.text


async def test_cancelled_sessions_are_not_offered(bot: BotHarness) -> None:
    await (await confirmed(bot, ALICE, "11:00")).tap("✅ Confirm")
    async with bot.sessionmaker() as db:
        await db.execute(update(Session).values(status="cancelled"))
        await db.commit()

    wizard = await confirmed(bot, BOB, "13:00")

    assert "Is this right?" in wizard.text


async def test_changing_price_forgets_the_merge_choice(bot: BotHarness) -> None:
    await (await confirmed(bot, ALICE, "11:00")).tap("✅ Confirm")
    wizard = await confirmed(bot, BOB, "13:00")
    await wizard.tap("➕ Add to 11:00–13:00")

    await wizard.tap("✏️ Change price")
    await wizard.tap("1h")
    await wizard.tap("$7.40")

    assert "There's already a session" in wizard.text
