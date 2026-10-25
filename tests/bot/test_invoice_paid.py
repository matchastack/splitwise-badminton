from sqlalchemy import select

from badminton.bot.handlers.invoice import REPLACED
from badminton.db.models import AuditLog, Transfer
from badminton.domain.timeutil import utc_now
from badminton.services.repo import get_balances
from tests.conftest import BotHarness
from tests.fake_telegram import ADMIN_ID, callback_update, text_update, user
from tests.services.test_invoice import DEC_1, NOV_1, invoice_for, seed

ALICE = user(ADMIN_ID, "alice", "alice_handle")
BOB = user(222, "bob")
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

    assert set(card.buttons()) == {"✅ bob → alice $12.50 paid", "✅ carol → alice $12.50 paid"}
    assert card.current["text"].endswith("Tap ✅ once you've paid.")


async def test_payer_marks_paid_and_can_undo(bot: BotHarness) -> None:
    card = await october_card(bot)

    await card.tap("✅ bob → alice $12.50 paid", BOB)

    assert card.alert() == "Marked as paid ✅"
    assert (
        '✅ <s><a href="tg://user?id=222">bob</a> → @alice_handle $12.50</s>'
        in card.current["text"]
    )
    assert "↩️ Undo bob → alice" in card.buttons()
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

    await card.tap("↩️ Undo bob → alice", BOB)

    assert card.alert() == "Marked as unpaid"
    assert "✅ bob → alice $12.50 paid" in card.buttons()
    async with bot.sessionmaker() as db:
        assert await db.scalar(select(Transfer)) is None


async def test_payee_and_admin_can_mark_but_others_cannot(bot: BotHarness) -> None:
    card = await october_card(bot)

    await card.tap("✅ carol → alice $12.50 paid", STRANGER)
    assert card.alert() == "Only carol, alice or the admin can mark this."

    await card.tap("✅ carol → alice $12.50 paid", ALICE)  # alice is both the payee and the admin
    assert card.alert() == "Marked as paid ✅"


async def test_replaced_invoice_has_no_buttons(bot: BotHarness) -> None:
    card = await october_card(bot)
    stale = card.buttons()["✅ carol → alice $12.50 paid"]

    await invoice_for(bot, "2026-11", DEC_1)

    assert card.buttons() == {}
    await bot.send(callback_update(stale, sender=ALICE, on_message=card.message))
    assert card.alert() == REPLACED


async def test_balance_lists_unpaid_lines(bot: BotHarness) -> None:
    await october_card(bot)

    await bot.send(text_update("/balance", sender=BOB, chat_id=BOB["id"]))
    bob = bot.telegram.last("sendMessage").params["text"]
    await bot.send(text_update("/balance", sender=ALICE, chat_id=ALICE["id"]))
    alice = bot.telegram.last("sendMessage").params["text"]

    assert bob.splitlines()[-1] == "🧾 To settle: pay @alice_handle $12.50"
    assert alice.splitlines()[-2:] == [
        '🧾 To settle: <a href="tg://user?id=222">bob</a> owes you $12.50',
        "🧾 To settle: carol owes you $12.50",
    ]
