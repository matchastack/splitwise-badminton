from datetime import date, timedelta

from sqlalchemy import select

from badminton.db.models import AuditLog, Booking, Member, Session
from badminton.domain.timeutil import fmt_date, fmt_session, utc_now
from badminton.services.repo import load_ledger
from tests.bot.attendance_helpers import post
from tests.bot.edit_helpers import ZY, EditCard
from tests.bot.test_edit_attendance import START, WHEN, setup
from tests.conftest import BotHarness
from tests.fake_telegram import GROUP_ID, message, message_update


async def open_booking(bot: BotHarness) -> EditCard:
    card = await EditCard(bot).open()
    await card.tap_starting(fmt_date(date(2026, 10, 4)))
    await card.tap("💵 Bookings")
    await card.tap("zy · 11:00 · 2h · $14.80")
    return card


async def the_booking(bot: BotHarness) -> Booking:
    async with bot.sessionmaker() as db:
        return await db.scalar(select(Booking))


async def test_change_price_through_a_reply(bot: BotHarness) -> None:
    await setup(bot)
    card = await open_booking(bot)
    assert "zy's booking: 11:00, 2h, $14.80" in card.text

    await card.tap("💵 Price")
    prompt = bot.telegram.last("sendMessage").message
    await bot.send(message_update(message(sender=ZY, text="16", reply_to=prompt)))

    assert (await the_booking(bot)).amount_cents == 1600
    assert bot.telegram.sent_texts(GROUP_ID)[-1] == (
        f"✏️ Admin changed zy's booking for <b>{WHEN}</b>: price $14.80 → $16.00\n"
        "💵 Court now $8.00 each (2 played)"
    )
    assert "zy's booking: 11:00, 2h, $16.00" in card.text
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
    await card.tap("hw")

    assert (await the_booking(bot)).payer_member_id == ids["hw"]
    assert bot.telegram.sent_texts(GROUP_ID)[-1].startswith(
        f"✏️ Admin changed zy's booking for <b>{WHEN}</b>: paid by zy → hw"
    )
    async with bot.sessionmaker() as db:
        ledger = await load_ledger(db, utc_now())
        zy = await db.scalar(select(Member.id).where(Member.display_name == "zy"))
    assert ledger.balance(ids["hw"]) == 1480 - 740
    assert ledger.balance(zy) == 0


async def test_change_hours_moves_the_session_end(bot: BotHarness) -> None:
    session_id, _ = await setup(bot)
    card_message = await post(bot, session_id)
    card = await open_booking(bot)

    await card.tap("⏱ Hours")
    await card.tap("Other…")
    prompt = bot.telegram.last("sendMessage").message
    await bot.send(message_update(message(sender=ZY, text="3", reply_to=prompt)))

    async with bot.sessionmaker() as db:
        session = await db.get(Session, session_id)
    assert session.ends_at == START + timedelta(hours=3)
    announcement = bot.telegram.sent_texts(GROUP_ID)[-1].splitlines()
    assert announcement[:2] == [
        f"✏️ Admin changed zy's booking for <b>{WHEN}</b>: 2h → 3h",
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
