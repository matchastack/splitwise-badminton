from sqlalchemy import select

from badminton.bot.admin import NOT_ADMIN
from badminton.db.models import AuditLog, Member, Transfer
from badminton.domain.timeutil import utc_now
from badminton.services.repo import load_ledger
from tests.bot.edit_helpers import ZY
from tests.conftest import BotHarness
from tests.fake_telegram import (
    GROUP_ID,
    callback_update,
    message,
    message_update,
    text_update,
    user,
)
from tests.services.test_invoice import NOV_1, invoice_for, seed


class SettleCard:
    def __init__(self, bot: BotHarness) -> None:
        self.bot = bot
        self.message: dict = {}

    async def open(self) -> "SettleCard":
        await self.bot.send(text_update("/settle", sender=ZY))
        self.message = self.bot.telegram.last("sendMessage").message
        return self

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

    async def tap(self, label: str) -> None:
        await self.bot.send(
            callback_update(self.buttons()[label], sender=ZY, on_message=self.message)
        )

    async def answer(self, text: str) -> None:
        prompt = self.bot.telegram.last("sendMessage").message
        await self.bot.send(message_update(message(sender=ZY, text=text, reply_to=prompt)))


async def members(bot: BotHarness, *names: str) -> dict[str, int]:
    async with bot.sessionmaker() as db:
        rows = [Member(display_name=name) for name in names]
        db.add_all(rows)
        await db.commit()
        return {m.display_name: m.id for m in rows}


async def test_settle_is_admin_only(bot: BotHarness) -> None:
    await bot.send(text_update("/settle", sender=user(222, "shawn")))

    assert bot.telegram.sent_texts() == [NOT_ADMIN]


async def test_record_a_payment(bot: BotHarness) -> None:
    ids = await members(bot, "shawn", "hw")
    card = await SettleCard(bot).open()

    await card.tap("shawn")
    assert "shawn" not in card.buttons()  # can't pay yourself
    await card.tap("hw")
    await card.answer("lots")
    assert bot.telegram.last("sendMessage").params["text"].startswith("⚠️ Enter an amount")
    await card.answer("50")
    assert "Amount: $50.00" in card.current["text"]
    await card.tap("✅ Record")

    async with bot.sessionmaker() as db:
        transfer = await db.scalar(select(Transfer))
        ledger = await load_ledger(db, utc_now())
        audit = await db.scalar(select(AuditLog))
    assert (transfer.from_member_id, transfer.to_member_id, transfer.amount_cents) == (
        ids["shawn"],
        ids["hw"],
        5000,
    )
    assert transfer.invoice_line_id is None
    assert ledger.balances == {ids["shawn"]: 5000, ids["hw"]: -5000}
    assert audit.action == "transfer.add"
    assert card.current["text"] == "✅ Recorded: shawn paid hw $50.00."
    assert (
        bot.telegram.sent_texts(GROUP_ID)[-1]
        == "💸 Admin recorded a payment: shawn paid hw $50.00."
    )


async def test_same_person_on_both_sides_is_refused(bot: BotHarness) -> None:
    ids = await members(bot, "shawn", "hw")
    card = await SettleCard(bot).open()
    await card.tap("shawn")
    draft_id = card.buttons()["hw"].split(":")[1]

    await bot.send(
        callback_update(f"st:{draft_id}:t:{ids['shawn']}", sender=ZY, on_message=card.message)
    )

    assert bot.telegram.last("answerCallbackQuery").params["text"] == "Pick two different people."


async def test_matching_invoice_line_is_marked_paid(bot: BotHarness) -> None:
    await seed(bot)
    await invoice_for(bot, "2026-10", NOV_1)
    invoice_card_id = bot.telegram.last("sendMessage").message["message_id"]
    card = await SettleCard(bot).open()

    await card.tap("nic")
    await card.tap("zy")
    await card.answer("12.50")
    await card.tap("✅ Record")

    async with bot.sessionmaker() as db:
        transfer = await db.scalar(select(Transfer))
    assert transfer.invoice_line_id is not None
    assert bot.telegram.sent_texts(GROUP_ID)[-1].endswith("It settles their line on the invoice.")
    invoice_edit = [
        c
        for c in bot.telegram.calls_to("editMessageText")
        if c.params["message_id"] == invoice_card_id
    ][-1]
    assert "✅ <s>nic → @zy_handle $12.50</s>" in invoice_edit.params["text"]
