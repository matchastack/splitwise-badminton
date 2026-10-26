from datetime import date, timedelta

from sqlalchemy import select

from badminton.bot.membership import OPEN_CHAT
from badminton.db.models import AuditLog, Booking, Member, Session
from badminton.domain.timeutil import fmt_date, fmt_session, utc_now
from badminton.services.repo import load_ledger
from tests.bot.attendance_helpers import post
from tests.bot.booking_helpers import BOB, Card, confirmed, future_day
from tests.bot.edit_helpers import ALICE, EditCard
from tests.bot.test_edit_attendance import START, WHEN, setup
from tests.conftest import BotHarness
from tests.fake_telegram import GROUP_ID, callback_update, message, message_update


async def open_booking(bot: BotHarness) -> EditCard:
    card = await EditCard(bot).open()
    await card.tap_starting(fmt_date(date(2026, 10, 4)))
    await card.tap("💵 Bookings")
    await card.tap("alice · 11:00 · 2h · $14.80")
    return card


async def the_booking(bot: BotHarness) -> Booking:
    async with bot.sessionmaker() as db:
        return await db.scalar(select(Booking))


def card_edits(bot: BotHarness, card: EditCard) -> list:
    return [
        call
        for call in bot.telegram.calls_to("editMessageText")
        if call.params["message_id"] == card.message["message_id"]
    ]


async def test_change_price_through_a_reply(bot: BotHarness) -> None:
    await setup(bot)
    card = await open_booking(bot)
    assert "alice's booking: 11:00, 2h, $14.80" in card.text

    await card.tap("💵 Price")
    prompt = bot.telegram.last("sendMessage").message
    await bot.send(message_update(message(sender=ALICE, text="16", reply_to=prompt)))

    assert (await the_booking(bot)).amount_cents == 1600
    assert bot.telegram.sent_texts(GROUP_ID)[-1] == (
        f"✏️ Admin changed alice's booking for <b>{WHEN}</b>: price $14.80 → $16.00\n"
        "💵 Court now $8.00 each (2 played)"
    )
    assert "alice's booking: 11:00, 2h, $16.00" in card.text
    async with bot.sessionmaker() as db:
        row = await db.scalar(select(AuditLog))
    assert (
        row.action,
        row.payload["before"]["amount_cents"],
        row.payload["after"]["amount_cents"],
    ) == (
        "booking.edit",
        1480,
        1600,
    )


async def test_change_payer(bot: BotHarness) -> None:
    _, ids = await setup(bot)
    card = await open_booking(bot)

    await card.tap("👤 Payer")
    await card.tap("dave")

    assert (await the_booking(bot)).payer_member_id == ids["dave"]
    assert bot.telegram.sent_texts(GROUP_ID)[-1].startswith(
        f"✏️ Admin changed alice's booking for <b>{WHEN}</b>: paid by alice → dave"
    )
    async with bot.sessionmaker() as db:
        ledger = await load_ledger(db, utc_now())
        alice = await db.scalar(select(Member.id).where(Member.display_name == "alice"))
    assert ledger.balance(ids["dave"]) == 1480 - 740
    assert ledger.balance(alice) == 0


async def test_a_second_payer_tap_changes_nothing(bot: BotHarness) -> None:
    """The second tap of a double-tap waits for the first, then finds the payer set."""
    await setup(bot)
    card = await open_booking(bot)
    await card.tap("👤 Payer")
    dave = callback_update(card.buttons()["dave"], sender=ALICE, on_message=card.message)
    await bot.send(dave)
    sent, edits = len(bot.telegram.sent_texts(GROUP_ID)), len(card_edits(bot, card))

    await bot.send(dave)

    assert len(bot.telegram.sent_texts(GROUP_ID)) == sent  # announced once
    assert len(card_edits(bot, card)) == edits
    assert "dave's booking" in card.text
    async with bot.sessionmaker() as db:
        assert len((await db.scalars(select(AuditLog))).all()) == 1


async def test_picking_the_payer_it_already_has_changes_nothing(bot: BotHarness) -> None:
    await setup(bot)
    card = await open_booking(bot)
    sent = len(bot.telegram.sent_texts(GROUP_ID))

    await card.tap("👤 Payer")
    await card.tap("alice")

    assert len(bot.telegram.sent_texts(GROUP_ID)) == sent
    assert card.text.endswith("What should change?")  # back on the booking menu
    async with bot.sessionmaker() as db:
        assert await db.scalar(select(AuditLog)) is None


async def test_change_hours_moves_the_session_end(bot: BotHarness) -> None:
    session_id, _ = await setup(bot)
    card_message = await post(bot, session_id)
    card = await open_booking(bot)

    await card.tap("⏱ Hours")
    await card.tap("Other…")
    prompt = bot.telegram.last("sendMessage").message
    await bot.send(message_update(message(sender=ALICE, text="3", reply_to=prompt)))

    async with bot.sessionmaker() as db:
        session = await db.get(Session, session_id)
    assert session.ends_at == START + timedelta(hours=3)
    announcement = bot.telegram.sent_texts(GROUP_ID)[-1].splitlines()
    assert announcement[:2] == [
        f"✏️ Admin changed alice's booking for <b>{WHEN}</b>: 2h → 3h",
        f"📅 The session is now {fmt_session(START, START + timedelta(hours=3))}",
    ]
    refreshed = [
        c.params["text"]
        for c in bot.telegram.calls_to("editMessageText")
        if c.params["message_id"] == card_message["message_id"]
    ]
    assert refreshed[-1].startswith(
        f"🏸 <b>{fmt_session(START, START + timedelta(hours=3))}</b> (3h)"
    )


async def test_cancel_session(bot: BotHarness) -> None:
    session_id, _ = await setup(bot)
    card_message = await post(bot, session_id)
    card = await EditCard(bot).open()
    await card.tap_starting(fmt_date(date(2026, 10, 4)))

    await card.tap("🗑 Cancel session")
    assert "Nobody is charged for it." in card.text
    await card.tap("Yes, cancel the session")

    async with bot.sessionmaker() as db:
        session = await db.get(Session, session_id)
        ledger = await load_ledger(db, utc_now())
        audit = await db.scalar(select(AuditLog))
    assert session.status == "cancelled"
    assert (await the_booking(bot)).cancelled_at is not None
    assert ledger.balances == {}
    assert audit.action == "session.cancel"
    assert card.text == f"🗑 Cancelled the session on {WHEN}."
    assert bot.telegram.sent_texts(GROUP_ID)[-1] == (
        f"🗑 Admin cancelled the session on <b>{WHEN}</b>. Nobody is charged for it."
    )
    closing = [
        c
        for c in bot.telegram.calls_to("editMessageText")
        if c.params["message_id"] == card_message["message_id"]
    ]
    assert closing[-1].params["text"] == f"❌ <b>Session cancelled</b>: {WHEN}"


async def test_keep_it_goes_back_to_the_menu(bot: BotHarness) -> None:
    await setup(bot)
    card = await EditCard(bot).open()
    await card.tap_starting(fmt_date(date(2026, 10, 4)))

    await card.tap("🗑 Cancel session")
    await card.tap("‹ Keep it")

    assert "What do you want to change?" in card.text


async def test_moving_on_withdraws_an_open_prompt(bot: BotHarness) -> None:
    await setup(bot)
    card = await open_booking(bot)
    await card.tap("💵 Price")
    prompt = bot.telegram.last("sendMessage").message

    await card.tap("‹ Bookings")
    await card.tap("‹ Back")
    await bot.send(message_update(message(sender=ALICE, text="20", reply_to=prompt)))

    deleted = [call.params["message_id"] for call in bot.telegram.calls_to("deleteMessage")]
    assert prompt["message_id"] in deleted
    assert (await the_booking(bot)).amount_cents == 1480
    assert not [t for t in bot.telegram.sent_texts(GROUP_ID) if t.startswith("✏️ Admin")]


async def booked_record(bot: BotHarness) -> Card:
    """bob's confirmed booking, 11:00–13:00 in two weeks for $14.80."""
    return await confirmed(bot, BOB, future_day(14))


async def test_a_corrected_booking_updates_its_record(bot: BotHarness) -> None:
    record = await booked_record(bot)
    card = await EditCard(bot).open()
    await card.tap_starting(fmt_date(future_day(14)))
    await card.tap("💵 Bookings")
    await card.tap("bob · 11:00 · 2h · $14.80")

    await card.tap("💵 Price")
    prompt = bot.telegram.last("sendMessage").message
    await bot.send(message_update(message(sender=ALICE, text="20", reply_to=prompt)))

    assert record.text == (
        f"📅 {fmt_date(future_day(14))}, 11:00–13:00 (2h)\n"
        "📍 Hougang Sport Hall · Court 11\n"
        "💵 $20.00, paid by @bob_t\n"
        "✏️ Corrected by the admin."
    )
    assert set(record.buttons()) == {OPEN_CHAT, "🗑 Cancel booking"}


async def test_cancelling_the_session_updates_its_records(bot: BotHarness) -> None:
    record = await booked_record(bot)
    card = await EditCard(bot).open()
    await card.tap_starting(fmt_date(future_day(14)))

    await card.tap("🗑 Cancel session")
    await card.tap("Yes, cancel the session")

    assert record.text.startswith(f"🗑 <s>📅 {fmt_date(future_day(14))}, 11:00–13:00 (2h)\n")
    assert record.text.endswith("paid by @bob_t</s>\nCancelled by alice with the session.")
    assert record.buttons() == {}
    unpinned = bot.telegram.last("unpinChatMessage").params
    assert unpinned["message_id"] == record.card["message_id"]
