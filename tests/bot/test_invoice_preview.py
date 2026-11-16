from datetime import date, datetime

import pytest
from sqlalchemy import func, select

from badminton.bot.admin import NOT_ADMIN
from badminton.bot.handlers import invoice as invoice_handlers
from badminton.db.models import Invoice, InvoiceBalance, InvoiceLine
from badminton.domain.timeutil import at_sgt
from badminton.services.monthly import PREVIEW_HEADER
from tests.conftest import BotHarness
from tests.fake_telegram import ADMIN_ID, GROUP_ID, text_update, user
from tests.services.test_invoice import DEC_1, NOV_1, invoice_for, seed
from tests.services.test_statements import enable_dms

ALICE = user(ADMIN_ID, "alice", "alice_handle")
BOB = user(222, "bob")
MID_NOVEMBER = at_sgt(date(2026, 11, 10), 12)


def at(monkeypatch: pytest.MonkeyPatch, now: datetime) -> None:
    monkeypatch.setattr(invoice_handlers, "utc_now", lambda: now)


async def preview(bot: BotHarness) -> str:
    await bot.send(text_update("/invoice_preview", sender=ALICE, chat_id=ADMIN_ID))
    return bot.telegram.sent_texts(ADMIN_ID)[-1]


def quoted_card(text: str) -> str:
    return text.split("<blockquote>", 1)[1].split("</blockquote>", 1)[0]


async def saved_rows(bot: BotHarness) -> tuple[int, int, int]:
    async with bot.sessionmaker() as db:
        return (
            await db.scalar(select(func.count()).select_from(Invoice)),
            await db.scalar(select(func.count()).select_from(InvoiceLine)),
            await db.scalar(select(func.count()).select_from(InvoiceBalance)),
        )


async def test_preview_shows_the_card_and_saves_and_sends_nothing(
    bot: BotHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed(bot)
    at(monkeypatch, MID_NOVEMBER)

    text = await preview(bot)

    assert text == (
        f"{PREVIEW_HEADER}\n"
        "\n"
        "<blockquote>"
        "🧾 <b>November 2026</b>\n"
        "🏸 2 sessions · court $30.00\n"
        "\n"
        "<b>Settle up</b>\n"
        '• <a href="tg://user?id=222">bob</a> → @alice_handle $12.50\n'
        "• carol → @alice_handle $12.50\n"
        "\n"
        "Tap ✅ once you've paid."
        "</blockquote>\n"
        "\n"
        "📬 No one would get a private statement."
    )
    assert "reply_markup" not in bot.telegram.last("sendMessage").params
    assert bot.telegram.sent_texts(GROUP_ID) == []
    assert await saved_rows(bot) == (0, 0, 0)


async def test_preview_matches_what_invoice_then_posts(
    bot: BotHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed(bot)
    await enable_dms(bot, 111, 222)
    at(monkeypatch, MID_NOVEMBER)

    text = await preview(bot)
    await bot.send(text_update("/invoice", sender=ALICE, chat_id=ADMIN_ID))

    group = bot.telegram.sent_texts(GROUP_ID)
    assert group == [quoted_card(text)]
    assert text.endswith("📬 Private statements would go to: alice, bob")
    statements = [
        call.params["chat_id"]
        for call in bot.telegram.calls_to("sendMessage")
        if call.params["text"].startswith("🧾 <b>Your November 2026 statement</b>")
    ]
    assert statements == [111, 222]


async def test_preview_early_in_the_month_shows_last_months_invoice(
    bot: BotHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed(bot)
    at(monkeypatch, at_sgt(date(2026, 11, 1), 8))

    text = await preview(bot)

    assert quoted_card(text).startswith("🧾 <b>October 2026</b>\n🏸 2 sessions · court $30.00")


async def test_preview_counts_carried_lines_and_changes_nothing(
    bot: BotHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed(bot)
    october = await invoice_for(bot, "2026-10", NOV_1)
    await enable_dms(bot, 111, 222)
    edits = len(bot.telegram.calls_to("editMessageText"))
    at(monkeypatch, DEC_1)

    text = await preview(bot)

    assert quoted_card(text).startswith("🧾 <b>November 2026</b>\n🏸 0 sessions · court $0.00")
    assert text.splitlines()[-2:] == [
        "↪️ 2 unpaid lines on the October 2026 invoice would be carried into this one.",
        "📬 Private statements would go to: alice, bob",
    ]
    assert await saved_rows(bot) == (1, 2, 3)
    async with bot.sessionmaker() as db:
        carried = await db.scalars(
            select(InvoiceLine.carried_at).where(InvoiceLine.invoice_id == october.id)
        )
        assert list(carried) == [None, None]
    assert len(bot.telegram.calls_to("editMessageText")) == edits  # October's card untouched
    assert len(bot.telegram.sent_texts(GROUP_ID)) == 1


async def test_preview_when_everyone_is_square(
    bot: BotHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    at(monkeypatch, MID_NOVEMBER)

    text = await preview(bot)

    assert quoted_card(text) == (
        "🧾 <b>November 2026</b>\n🏸 0 sessions · court $0.00\n\nEveryone is square 🎉"
    )


async def test_preview_once_the_month_is_invoiced(
    bot: BotHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed(bot)
    at(monkeypatch, MID_NOVEMBER)
    await bot.send(text_update("/invoice", sender=ALICE, chat_id=ADMIN_ID))

    text = await preview(bot)

    assert text == (
        "/invoice would post nothing: November 2026 already has an invoice. "
        "New activity goes on next month's invoice."
    )


async def test_preview_is_admin_only(bot: BotHarness) -> None:
    await seed(bot)

    await bot.send(text_update("/invoice_preview", sender=BOB, chat_id=BOB["id"]))

    assert bot.telegram.sent_texts() == [NOT_ADMIN]
