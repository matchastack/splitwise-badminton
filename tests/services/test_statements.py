from datetime import date

from sqlalchemy import select, update

from badminton.db.models import Attendance, Member, OpeningBalance
from badminton.domain.timeutil import at_sgt
from tests.conftest import BotHarness
from tests.services.test_invoice import DEC_1, NOV_1, invoice_for, seed


async def enable_dms(bot: BotHarness, *telegram_ids: int) -> None:
    async with bot.sessionmaker() as db:
        await db.execute(
            update(Member).where(Member.telegram_user_id.in_(telegram_ids)).values(dm_enabled=True)
        )
        await db.commit()


def dms(bot: BotHarness) -> dict[int, str]:
    return {
        call.params["chat_id"]: call.params["text"]
        for call in bot.telegram.calls_to("sendMessage")
        if call.params["chat_id"] > 0 and call.result is not None
    }


async def test_statements_explain_each_members_amount(bot: BotHarness) -> None:
    await seed(bot)
    await enable_dms(bot, 111, 222)

    await invoice_for(bot, "2026-10", NOV_1)

    statements = dms(bot)
    assert set(statements) == {111, 222}  # nic is a guest without Telegram
    assert statements[222] == (
        "🧾 <b>Your October 2026 statement</b>\n"
        "\n"
        "Opening balance: +$0.00\n"
        "🏸 Sun 4 Oct (2h): court −$5.00\n"
        "🏸 Sun 11 Oct (2h): court −$7.50\n"
        "<b>Closing balance</b>: you owe $12.50.\n"
        "\n"
        "👉 Pay @zy_handle $12.50, then tap ✅ on the group card."
    )
    assert statements[111].splitlines()[3:5] == [
        "💵 You paid for the court (Sun 4 Oct): +$15.00",
        "🏸 Sun 4 Oct (2h): court −$5.00",
    ]
    assert statements[111].endswith(
        '👉 <a href="tg://user?id=222">shawn</a> will pay you $12.50.\n👉 nic will pay you $12.50.'
    )


async def test_members_without_dms_or_activity_get_nothing(bot: BotHarness) -> None:
    await seed(bot)
    async with bot.sessionmaker() as db:
        db.add(Member(display_name="yt", telegram_user_id=666, dm_enabled=True))
        await db.commit()

    await invoice_for(bot, "2026-10", NOV_1)

    assert dms(bot) == {}


async def test_statement_shows_the_balance_carried_over_from_the_spreadsheet(
    bot: BotHarness,
) -> None:
    ids = await seed(bot)
    await enable_dms(bot, 222)
    async with bot.sessionmaker() as db:
        imported = at_sgt(date(2026, 10, 23), 9)
        db.add_all(
            [
                OpeningBalance(member_id=ids["shawn"], amount_cents=1452, created_at=imported),
                OpeningBalance(member_id=ids["nic"], amount_cents=-1452, created_at=imported),
            ]
        )
        await db.commit()

    await invoice_for(bot, "2026-10", NOV_1)

    assert dms(bot)[222].splitlines()[2:7] == [
        "Opening balance: +$0.00",
        "🏸 Sun 4 Oct (2h): court −$5.00",
        "🏸 Sun 11 Oct (2h): court −$7.50",
        "📒 Carried over from the spreadsheet: +$14.52",
        "<b>Closing balance</b>: you're owed $2.02.",
    ]


async def test_blocked_member_is_skipped_and_dms_turned_off(bot: BotHarness) -> None:
    await seed(bot)
    await enable_dms(bot, 111, 222)
    bot.telegram.fail("sendMessage", 403, "Forbidden: bot was blocked by the user", chat_id=222)

    await invoice_for(bot, "2026-10", NOV_1)

    assert set(dms(bot)) == {111}
    async with bot.sessionmaker() as db:
        shawn = await db.scalar(select(Member).where(Member.telegram_user_id == 222))
        assert shawn.dm_enabled is False


async def test_corrections_to_an_invoiced_month_show_as_adjustments(bot: BotHarness) -> None:
    ids = await seed(bot)
    await enable_dms(bot, 222)
    await invoice_for(bot, "2026-10", NOV_1)
    # After October was invoiced, the admin removes nic from the 4 Oct session.
    async with bot.sessionmaker() as db:
        await db.execute(
            Attendance.__table__.delete().where(
                Attendance.member_id == ids["nic"],
                Attendance.session_id == 1,
            )
        )
        await db.commit()
    bot.telegram.clear()

    await invoice_for(bot, "2026-11", DEC_1)

    lines = dms(bot)[222].splitlines()
    assert lines[2:4] == ["Opening balance: −$12.50", "Corrections to earlier months: −$2.50"]
    assert "<b>Closing balance</b>: you owe $15.00." in lines
