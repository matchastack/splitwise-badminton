from datetime import date

import pytest
from sqlalchemy import func, select

from badminton.bot.admin import NOT_ADMIN
from badminton.db.models import Invoice
from badminton.domain.timeutil import at_sgt, fmt_period, period_of, utc_now
from badminton.services.monthly import monthly_invoice
from badminton.services.tick import TickContext
from tests.conftest import TEST_SETTINGS, BotHarness
from tests.fake_telegram import ADMIN_ID, GROUP_ID, text_update, user
from tests.services.test_invoice import seed

ZY = user(ADMIN_ID, "zy", "zy_handle")
SHAWN = user(222, "shawn")


async def periods(bot: BotHarness) -> list[str]:
    async with bot.sessionmaker() as db:
        return list(await db.scalars(select(Invoice.period).order_by(Invoice.period)))


async def tick(bot: BotHarness, day: date, hour: int = 10) -> None:
    context = TickContext(bot.application.bot, TEST_SETTINGS, bot.sessionmaker, at_sgt(day, hour))
    await monthly_invoice(context)


async def test_first_of_the_month_invoices_the_previous_month_once(bot: BotHarness) -> None:
    await seed(bot)

    await tick(bot, date(2026, 11, 1))
    await tick(bot, date(2026, 11, 1), hour=11)

    assert await periods(bot) == ["2026-10"]
    cards = [t for t in bot.telegram.sent_texts(GROUP_ID) if t.startswith("🧾")]
    assert len(cards) == 1


@pytest.mark.parametrize("day", [2, 7])
async def test_missed_runs_are_caught_up_in_the_first_week(bot: BotHarness, day: int) -> None:
    await seed(bot)

    await tick(bot, date(2026, 11, day))

    assert await periods(bot) == ["2026-10"]


@pytest.mark.parametrize("day", [date(2026, 11, 8), date(2026, 10, 21)])
async def test_no_invoice_outside_the_catch_up_window(bot: BotHarness, day: date) -> None:
    await seed(bot)

    await tick(bot, day)

    assert await periods(bot) == []


async def test_admin_invoice_command(bot: BotHarness) -> None:
    await seed(bot)
    this_month = fmt_period(period_of(utc_now()))

    await bot.send(text_update("/invoice", sender=ZY))
    assert await periods(bot) == [period_of(utc_now())]
    assert bot.telegram.sent_texts(GROUP_ID)[-1].startswith(f"🧾 <b>{this_month}</b>")

    await bot.send(text_update("/invoice", sender=ZY, chat_id=ADMIN_ID))
    assert bot.telegram.last("sendMessage").params["text"] == (
        f"{this_month} already has an invoice. New activity goes on next month's invoice."
    )
    async with bot.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(Invoice)) == 1


async def test_invoice_command_in_a_dm_confirms(bot: BotHarness) -> None:
    await bot.send(text_update("/invoice", sender=ZY, chat_id=ADMIN_ID))

    reply = bot.telegram.last("sendMessage").params
    assert (reply["chat_id"], reply["text"]) == (
        ADMIN_ID,
        f"🧾 Posted the {fmt_period(period_of(utc_now()))} invoice in the group.",
    )


async def test_invoice_command_is_admin_only(bot: BotHarness) -> None:
    await bot.send(text_update("/invoice", sender=SHAWN))

    assert bot.telegram.sent_texts() == [NOT_ADMIN]
    assert await periods(bot) == []
