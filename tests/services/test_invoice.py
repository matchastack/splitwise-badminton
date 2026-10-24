from datetime import date, timedelta

import pytest
from sqlalchemy import func, select
from telegram.error import NetworkError

from badminton.db.models import (
    Attendance,
    Booking,
    Invoice,
    InvoiceBalance,
    InvoiceLine,
    Member,
    Session,
    Transfer,
)
from badminton.domain.timeutil import at_sgt
from badminton.services.monthly import run_invoice
from tests.conftest import TEST_SETTINGS, BotHarness
from tests.fake_telegram import GROUP_ID

NOV_1 = at_sgt(date(2026, 11, 1), 10)
DEC_1 = at_sgt(date(2026, 12, 1), 10)


async def seed(bot: BotHarness) -> dict[str, int]:
    """alice booked two $15 sessions in October; bob and carol played both, alice one."""
    async with bot.sessionmaker() as db:
        alice = Member(display_name="alice", telegram_user_id=111, username="alice_handle")
        bob = Member(display_name="bob", telegram_user_id=222)
        carol = Member(display_name="carol")
        db.add_all([alice, bob, carol])
        await db.flush()
        for day, players in [(4, [alice, bob, carol]), (11, [bob, carol])]:
            start = at_sgt(date(2026, 10, day), 11)
            session = Session(starts_at=start, ends_at=start + timedelta(hours=2))
            db.add(session)
            await db.flush()
            db.add(
                Booking(
                    session_id=session.id,
                    payer_member_id=alice.id,
                    submitted_by_member_id=alice.id,
                    starts_at=start,
                    minutes=120,
                    amount_cents=1500,
                )
            )
            db.add_all(
                Attendance(session_id=session.id, member_id=p.id, status="in") for p in players
            )
        await db.commit()
        return {"alice": alice.id, "bob": bob.id, "carol": carol.id}


async def invoice_for(bot: BotHarness, period: str, now) -> Invoice | None:
    async with bot.sessionmaker() as db:
        return await run_invoice(bot.application.bot, db, TEST_SETTINGS, period=period, now=now)


async def test_invoice_lists_fewest_transfers_with_handles(bot: BotHarness) -> None:
    ids = await seed(bot)

    invoice = await invoice_for(bot, "2026-10", NOV_1)

    # alice: +3000 − 500 = +2500; bob and carol: −500 − 750 = −1250 each.
    card = bot.telegram.last("sendMessage").params
    assert card["chat_id"] == GROUP_ID
    assert card["text"] == (
        "🧾 <b>October 2026</b>\n"
        "🏸 2 sessions · court $30.00\n"
        "\n"
        "<b>Settle up</b>\n"
        '• <a href="tg://user?id=222">bob</a> → @alice_handle $12.50\n'
        "• carol → @alice_handle $12.50\n"
        "\n"
        "Tap ✅ once you've paid."
    )
    async with bot.sessionmaker() as db:
        stored = await db.get(Invoice, invoice.id)
        assert stored.card_message_id == bot.telegram.last("sendMessage").message["message_id"]
        balances = {b.member_id: b.balance_cents for b in await db.scalars(select(InvoiceBalance))}
        assert balances == {ids["alice"]: 2500, ids["bob"]: -1250, ids["carol"]: -1250}


async def test_each_period_is_invoiced_once(bot: BotHarness) -> None:
    await seed(bot)

    assert await invoice_for(bot, "2026-10", NOV_1) is not None
    assert await invoice_for(bot, "2026-10", NOV_1 + timedelta(hours=1)) is None

    async with bot.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(Invoice)) == 1
        assert await db.scalar(select(func.count()).select_from(InvoiceLine)) == 2
    assert len(bot.telegram.calls_to("sendMessage")) == 1


async def test_everyone_square(bot: BotHarness) -> None:
    await invoice_for(bot, "2026-10", NOV_1)

    assert bot.telegram.last("sendMessage").params["text"].endswith("Everyone is square 🎉")


async def test_unpaid_lines_are_carried_into_the_next_invoice(bot: BotHarness) -> None:
    ids = await seed(bot)
    october = await invoice_for(bot, "2026-10", NOV_1)
    async with bot.sessionmaker() as db:
        paid_line = await db.scalar(
            select(InvoiceLine).where(InvoiceLine.from_member_id == ids["bob"])
        )
        db.add(
            Transfer(
                from_member_id=ids["bob"],
                to_member_id=ids["alice"],
                amount_cents=1250,
                invoice_line_id=paid_line.id,
            )
        )
        await db.commit()

    november = await invoice_for(bot, "2026-11", DEC_1)

    async with bot.sessionmaker() as db:
        october_lines = {
            line.from_member_id: line.carried_at
            for line in await db.scalars(
                select(InvoiceLine).where(InvoiceLine.invoice_id == october.id)
            )
        }
        november_lines = list(
            await db.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == november.id))
        )
    assert october_lines[ids["bob"]] is None  # paid, so not carried
    assert october_lines[ids["carol"]] == DEC_1
    assert [(line.from_member_id, line.amount_cents) for line in november_lines] == [
        (ids["carol"], 1250)
    ]

    old_card = bot.telegram.last("editMessageText").params
    assert old_card["message_id"] == october.card_message_id
    assert old_card["text"].endswith(
        '✅ <s><a href="tg://user?id=222">bob</a> → @alice_handle $12.50</s>\n'
        "↪️ carol → @alice_handle $12.50 (carried into November 2026)"
    )
    november_card = bot.telegram.last("sendMessage").params["text"]
    assert november_card.startswith("🧾 <b>November 2026</b>\n🏸 0 sessions · court $0.00")


async def test_an_invoice_whose_card_fails_is_retried(bot: BotHarness) -> None:
    ids = await seed(bot)
    october = await invoice_for(bot, "2026-10", NOV_1)
    bot.telegram.fail("sendMessage", 502, "Bad Gateway", chat_id=GROUP_ID)

    with pytest.raises(NetworkError):
        await invoice_for(bot, "2026-11", DEC_1)

    async with bot.sessionmaker() as db:
        assert list(await db.scalars(select(Invoice.period))) == ["2026-10"]
        assert await db.scalar(select(func.count()).select_from(InvoiceBalance)) == 3
        carried = await db.scalars(
            select(InvoiceLine.carried_at).where(InvoiceLine.invoice_id == october.id)
        )
        assert list(carried) == [None, None]

    retry = DEC_1 + timedelta(days=1)
    november = await invoice_for(bot, "2026-11", retry)

    assert november is not None
    assert november.card_message_id == bot.telegram.last("sendMessage").message["message_id"]
    async with bot.sessionmaker() as db:
        carried = await db.scalars(
            select(InvoiceLine.carried_at)
            .where(InvoiceLine.invoice_id == october.id)
            .order_by(InvoiceLine.from_member_id)
        )
        assert list(carried) == [retry, retry]
        lines = await db.scalars(
            select(InvoiceLine.from_member_id).where(InvoiceLine.invoice_id == november.id)
        )
        assert sorted(lines) == sorted([ids["bob"], ids["carol"]])
