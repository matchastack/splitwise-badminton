from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from badminton.bot.handlers.booking_cancel import STARTED
from badminton.db.models import AuditLog, Booking, Session
from badminton.domain.timeutil import at_sgt, fmt_date, utc_now
from tests.bot.booking_helpers import ALICE, BOB, CAROL, Card, book, future_day
from tests.conftest import BotHarness
from tests.fake_telegram import GROUP_ID, callback_update

DAY = future_day(14)


async def booked(
    bot: BotHarness, sender: dict = BOB, hour: str = "11:00", hours: str = "2h"
) -> Card:
    """Book and confirm, joining the 11:00–13:00 session if there is one; returns the record."""
    wizard = await book(bot, sender, DAY, hour=hour, hours=hours)
    if "➕ Add to 11:00–13:00" in wizard.buttons():
        await wizard.tap("➕ Add to 11:00–13:00")
    await wizard.tap("✅ Confirm")
    return wizard.record()


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
    assert record.text == (
        f"🗑 <s>📅 {fmt_date(DAY)}, 11:00–13:00 (2h)\n"
        "📍 Hougang Sport Hall · Court 11\n"
        "💵 $14.80, paid by @bob_t</s>\n"
        "Cancelled by bob."
    )
    assert record.buttons() == {}
    unpinned = bot.telegram.last("unpinChatMessage").params
    assert (unpinned["chat_id"], unpinned["message_id"]) == (GROUP_ID, record.card["message_id"])
    assert bot.telegram.sent_texts(GROUP_ID)[-1] == (
        f"🗑 bob cancelled @bob_t's booking for {fmt_date(DAY)}, 11:00–13:00 ($14.80).\n"
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

    await record.tap("🗑 Cancel booking", sender=CAROL)
    assert record.last_alert() == "Only bob or the admin can cancel this booking."

    await record.tap("🗑 Cancel booking", sender=ALICE)
    await record.tap("Yes, cancel it", sender=ALICE)
    assert (await only_session(bot)).status == "cancelled"
    assert record.text.endswith("Cancelled by alice.")


async def test_cannot_cancel_once_started(bot: BotHarness) -> None:
    record = await booked(bot)
    started = utc_now() - timedelta(minutes=5)
    async with bot.sessionmaker() as db:
        await db.execute(update(Booking).values(starts_at=started))
        await db.execute(update(Session).values(starts_at=started))
        await db.commit()

    await record.tap("🗑 Cancel booking")

    assert record.last_alert() == STARTED
    assert (await only_session(bot)).status == "scheduled"


async def test_cannot_cancel_a_later_booking_once_its_session_started(bot: BotHarness) -> None:
    await booked(bot, ALICE)
    second = await booked(bot, BOB, hour="13:00", hours="1h")
    started = utc_now() - timedelta(minutes=30)
    async with bot.sessionmaker() as db:  # play began at the first booking; the second is next
        session = await only_session(bot)
        first, later = sorted(session.bookings, key=lambda b: b.starts_at)
        await db.execute(update(Booking).where(Booking.id == first.id).values(starts_at=started))
        await db.execute(
            update(Booking)
            .where(Booking.id == later.id)
            .values(starts_at=started + timedelta(hours=2))
        )
        await db.execute(update(Session).values(starts_at=started))
        await db.commit()

    await second.tap("🗑 Cancel booking")

    assert second.last_alert() == STARTED
    async with bot.sessionmaker() as db:
        assert await db.scalar(select(Booking.cancelled_at).where(Booking.id == later.id)) is None


async def test_cancelling_one_of_two_bookings_shrinks_the_session(bot: BotHarness) -> None:
    await booked(bot, ALICE)
    second = await booked(bot, BOB, hour="13:00", hours="1h")
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

    await bot.send(callback_update(stale, sender=BOB, on_message=record.card))

    assert record.last_alert() == "This booking is already cancelled."


async def test_a_record_from_before_screenshots_were_posted_is_still_updated(
    bot: BotHarness,
) -> None:
    await booked(bot)
    await bot.application.bot.send_message(GROUP_ID, "✅ Court booked by @bob_t")
    old = bot.telegram.last("sendMessage").message
    async with bot.sessionmaker() as db:
        await db.execute(update(Booking).values(record_message_id=old["message_id"]))
        await db.commit()
    (booking,) = (await only_session(bot)).bookings
    record = Card(bot, BOB, old, len(bot.telegram.calls))

    await bot.send(callback_update(f"bkcy:{booking.id}", sender=BOB, on_message=old))

    assert record.text.startswith(f"🗑 <s>📅 {fmt_date(DAY)}, 11:00–13:00 (2h)\n")
    assert record.text.endswith("Cancelled by bob.")


async def test_a_network_error_unpinning_still_announces(bot: BotHarness) -> None:
    record = await booked(bot)
    await record.tap("🗑 Cancel booking")
    bot.telegram.fail("unpinChatMessage", 502, "Bad Gateway")

    await record.tap("Yes, cancel it")

    assert (await only_session(bot)).status == "cancelled"
    assert bot.telegram.sent_texts(GROUP_ID)[-1].endswith("That session is now cancelled.")
