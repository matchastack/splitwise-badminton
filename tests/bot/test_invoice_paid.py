from sqlalchemy import select

from badminton.bot.handlers.invoice import REPLACED
from badminton.db.models import AuditLog, Transfer
from badminton.domain.timeutil import utc_now
from badminton.services.repo import get_balances
from tests.conftest import BotHarness
from tests.fake_telegram import ADMIN_ID, callback_update, text_update, user
from tests.services.test_invoice import DEC_1, NOV_1, invoice_for, seed

ZY = user(ADMIN_ID, "zy", "zy_handle")
SHAWN = user(222, "shawn")
STRANGER = user(999, "stranger")


class InvoiceCard:
    def __init__(self, bot: BotHarness, message: dict) -> None:
        self.bot = bot
        self.message = message

    @property
    def current(self) -> dict:
        for call in reversed(self.bot.telegram.calls):
            edit = call.method == "editMessageText"
            if edit and call.params["message_id"] == self.message["message_id"]:
                return call.result
        return self.message

    def buttons(self) -> dict[str, str]:
        rows = self.current.get("reply_markup", {}).get("inline_keyboard", [])
        return {b["text"]: b["callback_data"] for row in rows for b in row}

    async def tap(self, label: str, sender: dict) -> None:
        data = self.buttons()[label]
        await self.bot.send(callback_update(data, sender=sender, on_message=self.message))

    def alert(self) -> str:
        return self.bot.telegram.last("answerCallbackQuery").params.get("text", "")


async def october_card(bot: BotHarness) -> InvoiceCard:
    await seed(bot)
    await invoice_for(bot, "2026-10", NOV_1)
    return InvoiceCard(bot, bot.telegram.last("sendMessage").message)


async def test_each_open_line_has_a_paid_button(bot: BotHarness) -> None:
    card = await october_card(bot)

    assert set(card.buttons()) == {"✅ shawn → zy $12.50 paid", "✅ nic → zy $12.50 paid"}
    assert card.current["text"].endswith("Tap ✅ once you've paid.")


async def test_payer_marks_paid_and_can_undo(bot: BotHarness) -> None:
    card = await october_card(bot)

    await card.tap("✅ shawn → zy $12.50 paid", SHAWN)

    assert card.alert() == "Marked as paid ✅"
    assert (
        '✅ <s><a href="tg://user?id=222">shawn</a> → @zy_handle $12.50</s>' in card.current["text"]
    )
    assert "↩️ Undo shawn → zy" in card.buttons()
    async with bot.sessionmaker() as db:
        transfer = await db.scalar(select(Transfer))
        balances = await get_balances(db, utc_now())
        audit = await db.scalar(select(AuditLog))
    assert (transfer.amount_cents, transfer.recorded_by_member_id) == (
        1250,
        transfer.from_member_id,
    )
    assert sorted(balances.values()) == [-1250, 0, 1250]
    assert audit.action == "invoice.paid"

    await card.tap("↩️ Undo shawn → zy", SHAWN)

    assert card.alert() == "Marked as unpaid"
    assert "✅ shawn → zy $12.50 paid" in card.buttons()
    async with bot.sessionmaker() as db:
        assert await db.scalar(select(Transfer)) is None


async def test_payee_and_admin_can_mark_but_others_cannot(bot: BotHarness) -> None:
    card = await october_card(bot)

    await card.tap("✅ nic → zy $12.50 paid", STRANGER)
    assert card.alert() == "Only nic, zy or the admin can mark this."

    await card.tap("✅ nic → zy $12.50 paid", ZY)  # zy is both the payee and the admin
    assert card.alert() == "Marked as paid ✅"


async def test_replaced_invoice_has_no_buttons(bot: BotHarness) -> None:
    card = await october_card(bot)
    stale = card.buttons()["✅ nic → zy $12.50 paid"]

    await invoice_for(bot, "2026-11", DEC_1)

    assert card.buttons() == {}
    await bot.send(callback_update(stale, sender=ZY, on_message=card.message))
    assert card.alert() == REPLACED


async def test_balance_lists_unpaid_lines(bot: BotHarness) -> None:
    await october_card(bot)

    await bot.send(text_update("/balance", sender=SHAWN))
    shawn = bot.telegram.last("sendMessage").params["text"]
    await bot.send(text_update("/balance", sender=ZY))
    zy = bot.telegram.last("sendMessage").params["text"]

    assert shawn.splitlines()[-1] == "🧾 To settle: pay @zy_handle $12.50"
    assert zy.splitlines()[-2:] == [
        '🧾 To settle: <a href="tg://user?id=222">shawn</a> owes you $12.50',
        "🧾 To settle: nic owes you $12.50",
    ]
