from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from badminton.db.models import AuditLog, Booking, Session
from badminton.domain.timeutil import at_sgt, fmt_date, utc_now
from tests.bot.booking_helpers import NIC, SHAWN, ZY, Wizard, book, future_day
from tests.conftest import BotHarness
from tests.fake_telegram import GROUP_ID, callback_update

DAY = future_day(14)


async def booked(
    bot: BotHarness, sender: dict = SHAWN, hour: str = "11:00", hours: str = "2h"
) -> Wizard:
    wizard = await book(bot, sender, DAY, hour=hour, hours=hours)
    await wizard.tap(next(label for label in wizard.buttons() if label.startswith("$")))
    if "➕ Add to 11:00–13:00" in wizard.buttons():
        await wizard.tap("➕ Add to 11:00–13:00")
    await wizard.tap("✅ Confirm")
    return wizard


async def only_session(bot: BotHarness) -> Session:
    async with bot.sessionmaker() as db:
        return await db.scalar(select(Session).options(selectinload(Session.bookings)))


async def test_submitter_cancels_after_confirming(bot: BotHarness) -> None:
    record = await booked(bot)

    await record.tap("🗑 Cancel booking")
    assert set(record.buttons()) == {"Yes, cancel it", "Keep it"}
    await record.tap("Yes, cancel it")

    session = await only_session(bot)
    assert session.status == "cancelled"
    assert session.bookings[0].cancelled_at is not None
    assert record.text.startswith("🗑 <s>✅ <b>Court booked</b> by @shawn_t")
    assert record.text.endswith("Cancelled by shawn.")
    assert bot.telegram.sent_texts(GROUP_ID)[-1] == (
        f"🗑 shawn cancelled @shawn_t's booking for {fmt_date(DAY)}, 11:00–13:00 ($14.80).\n"
        "That session is now cancelled."
    )
    async with bot.sessionmaker() as db:
        row = await db.scalar(select(AuditLog))
        assert (row.action, row.payload["booking_id"]) == ("booking.cancel", session.bookings[0].id)


async def test_keep_it_restores_the_button(bot: BotHarness) -> None:
    record = await booked(bot)

    await record.tap("🗑 Cancel booking")
    await record.tap("Keep it")

    assert set(record.buttons()) == {"🗑 Cancel booking"}
    assert (await only_session(bot)).status == "scheduled"


async def test_admin_can_cancel_but_others_cannot(bot: BotHarness) -> None:
    record = await booked(bot)

    await record.tap("🗑 Cancel booking", sender=NIC)
    assert record.last_alert() == "Only shawn or the admin can cancel this booking."

    await record.tap("🗑 Cancel booking", sender=ZY)
    await record.tap("Yes, cancel it", sender=ZY)
    assert (await only_session(bot)).status == "cancelled"
    assert record.text.endswith("Cancelled by zy.")


async def test_cannot_cancel_once_started(bot: BotHarness) -> None:
    record = await booked(bot)
    async with bot.sessionmaker() as db:
        await db.execute(update(Booking).values(starts_at=utc_now() - timedelta(minutes=5)))
        await db.commit()

    await record.tap("🗑 Cancel booking")

    assert record.last_alert() == "This booking has already started."
    assert (await only_session(bot)).status == "scheduled"


async def test_cancelling_one_of_two_bookings_shrinks_the_session(bot: BotHarness) -> None:
    await booked(bot, ZY)
    second = await booked(bot, SHAWN, hour="13:00", hours="1h")
    assert (await only_session(bot)).ends_at == at_sgt(DAY, 14)

    await second.tap("🗑 Cancel booking")
    await second.tap("Yes, cancel it")

    session = await only_session(bot)
    assert (session.status, session.starts_at, session.ends_at) == (
        "scheduled",
        at_sgt(DAY, 11),
        at_sgt(DAY, 13),
    )
    assert "That session is now cancelled." not in bot.telegram.sent_texts(GROUP_ID)[-1]


async def test_posted_attendance_card_is_closed(bot: BotHarness) -> None:
    record = await booked(bot)
    async with bot.sessionmaker() as db:
        await db.execute(update(Session).values(card_chat_id=GROUP_ID, card_message_id=4242))
        await db.commit()

    await record.tap("🗑 Cancel booking")
    await record.tap("Yes, cancel it")

    closing = [
        c for c in bot.telegram.calls_to("editMessageText") if c.params["message_id"] == 4242
    ]
    assert closing[0].params["text"] == f"❌ <b>Session cancelled</b>: {fmt_date(DAY)}, 11:00–13:00"


async def test_already_cancelled(bot: BotHarness) -> None:
    record = await booked(bot)
    await record.tap("🗑 Cancel booking")
    stale = record.buttons()["Yes, cancel it"]
    await record.tap("Yes, cancel it")

    await bot.send(callback_update(stale, sender=SHAWN, on_message=record.card))

    assert record.last_alert() == "This booking is already cancelled."
