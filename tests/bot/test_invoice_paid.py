import asyncio

from sqlalchemy import func, select, update
from telegram import Update

from badminton.bot.handlers.invoice import ALREADY_PAID, NOT_PENDING, REPLACED
from badminton.db.models import AuditLog, InvoiceLine, Member, Transfer
from badminton.domain.timeutil import utc_now
from badminton.services.repo import get_balances
from tests.conftest import BotHarness
from tests.fake_telegram import ADMIN_ID, GROUP_ID, callback_update, text_update, user
from tests.services.test_invoice import DEC_1, NOV_1, invoice_for, seed

ALICE = user(ADMIN_ID, "alice", "alice_handle")
BOB = user(222, "bob")
STRANGER = user(999, "stranger")

BOB_PAYS_ALICE = "✅ bob → alice $12.50 paid"
BOB_PAID = '<a href="tg://user?id=222">bob</a> → @alice_handle $12.50'
ASK_ALICE = (
    '💸 <a href="tg://user?id=222">bob</a> says they\'ve paid you $12.50 for October 2026. '
    "Have you received it?"
)


class InvoiceCard:
    """A bot message with buttons, the group card or a DM, as it now stands."""

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


async def october_card(
    bot: BotHarness, *, dms: tuple[int, ...] = (ALICE["id"], BOB["id"])
) -> InvoiceCard:
    """October's card, with DMs on for the members in ``dms``."""
    await seed(bot)
    async with bot.sessionmaker() as db:
        await db.execute(
            update(Member).where(Member.telegram_user_id.in_(dms)).values(dm_enabled=True)
        )
        await db.commit()
    await invoice_for(bot, "2026-10", NOV_1)
    posted = [c for c in bot.telegram.calls_to("sendMessage") if c.params["chat_id"] == GROUP_ID]
    return InvoiceCard(bot, posted[-1].message)


async def bob_says_paid(bot: BotHarness) -> tuple[InvoiceCard, InvoiceCard]:
    """bob taps ✅ paid. Returns the card and alice's DM asking her to confirm."""
    card = await october_card(bot)
    bot.telegram.clear()
    await card.tap(BOB_PAYS_ALICE, BOB)
    asked = bot.telegram.last("sendMessage")
    assert asked.params["chat_id"] == ALICE["id"]
    return card, InvoiceCard(bot, asked.message)


async def audit_actions(bot: BotHarness) -> list[str]:
    async with bot.sessionmaker() as db:
        return list(await db.scalars(select(AuditLog.action).order_by(AuditLog.id)))


async def test_each_open_line_has_a_paid_button(bot: BotHarness) -> None:
    card = await october_card(bot)

    assert set(card.buttons()) == {"✅ bob → alice $12.50 paid", "✅ carol → alice $12.50 paid"}
    assert card.current["text"].endswith("Tap ✅ once you've paid.")


async def test_payer_says_paid_and_payee_confirms_by_dm(bot: BotHarness) -> None:
    card, asked = await bob_says_paid(bot)

    assert card.alert() == "Waiting for alice to confirm ⏳"
    assert f"⏳ {BOB_PAID} (pending confirmation)" in card.current["text"]
    assert card.current["text"].endswith("Tap ✅ once you've paid.\nTap ☑️ once you've received it.")
    assert set(card.buttons()) == {
        "☑️ bob → alice $12.50 received",
        "↩️ Undo bob → alice",
        "✅ carol → alice $12.50 paid",
    }
    assert asked.current["text"] == ASK_ALICE
    assert set(asked.buttons()) == {"✅ Received", "❌ Not received"}
    async with bot.sessionmaker() as db:
        assert await db.scalar(select(Transfer)) is None

    await asked.tap("✅ Received", ALICE)

    assert card.alert() == "Marked as paid ✅"
    assert asked.current["text"] == f"{ASK_ALICE}\n\n✅ Confirmed. Thanks!"
    assert asked.buttons() == {}
    assert f"✅ <s>{BOB_PAID}</s>" in card.current["text"]
    assert "↩️ Undo bob → alice" in card.buttons()
    async with bot.sessionmaker() as db:
        transfer = await db.scalar(select(Transfer))
        alice = await db.scalar(select(Member).where(Member.display_name == "alice"))
        balances = await get_balances(db, utc_now())
    assert (transfer.amount_cents, transfer.recorded_by_member_id) == (1250, alice.id)
    assert sorted(balances.values()) == [-1250, 0, 1250]
    assert await audit_actions(bot) == ["invoice.claimed", "invoice.paid"]

    await card.tap("↩️ Undo bob → alice", BOB)

    assert card.alert() == "Marked as unpaid"
    assert BOB_PAYS_ALICE in card.buttons()
    async with bot.sessionmaker() as db:
        assert await db.scalar(select(Transfer)) is None


async def test_payee_says_not_received(bot: BotHarness) -> None:
    card, asked = await bob_says_paid(bot)

    await asked.tap("❌ Not received", ALICE)

    assert card.alert() == "Marked as not received"
    assert asked.current["text"] == f"{ASK_ALICE}\n\n❌ Not received."
    assert f"• {BOB_PAID}" in card.current["text"]
    assert BOB_PAYS_ALICE in card.buttons()
    told = bot.telegram.last("sendMessage").params
    assert told["chat_id"] == BOB["id"]
    assert told["text"] == (
        "❌ @alice_handle hasn't received your $12.50 for October 2026 yet, so it's unpaid "
        "again. Tap ✅ on the group card once you've paid."
    )
    async with bot.sessionmaker() as db:
        assert await db.scalar(select(Transfer)) is None
    assert await audit_actions(bot) == ["invoice.claimed", "invoice.unclaimed"]


async def test_payer_can_withdraw_but_not_confirm(bot: BotHarness) -> None:
    card, asked = await bob_says_paid(bot)
    pending = card.buttons()["☑️ bob → alice $12.50 received"]

    await card.tap("☑️ bob → alice $12.50 received", BOB)
    assert card.alert() == "Only alice or the admin can confirm this."

    await card.tap("↩️ Undo bob → alice", BOB)

    assert card.alert() == "Marked as unpaid"
    assert BOB_PAYS_ALICE in card.buttons()
    assert bot.telegram.last("sendMessage").params["chat_id"] == ALICE["id"]  # bob isn't told

    await asked.tap("✅ Received", ALICE)  # too late: the claim was withdrawn
    assert card.alert() == NOT_PENDING
    removed = bot.telegram.last("editMessageReplyMarkup").params
    assert removed["message_id"] == asked.message["message_id"]
    assert "reply_markup" not in removed
    await bot.send(callback_update(pending, sender=ALICE, on_message=card.message))
    assert card.alert() == NOT_PENDING
    async with bot.sessionmaker() as db:
        assert await db.scalar(select(Transfer)) is None


async def test_a_second_paid_tap_by_the_payer_waits(bot: BotHarness) -> None:
    card = await october_card(bot)
    paid = card.buttons()[BOB_PAYS_ALICE]
    await card.tap(BOB_PAYS_ALICE, BOB)
    asks = len(bot.telegram.calls_to("sendMessage"))

    await bot.send(callback_update(paid, sender=BOB, on_message=card.message))

    assert card.alert() == "Waiting for alice to confirm ⏳"
    assert len(bot.telegram.calls_to("sendMessage")) == asks
    assert "☑️ bob → alice $12.50 received" in card.buttons()


async def test_a_double_tap_on_received_records_one_payment(bot: BotHarness) -> None:
    _, asked = await bob_says_paid(bot)
    tap = callback_update(asked.buttons()["✅ Received"], sender=ALICE, on_message=asked.message)
    async with bot.sessionmaker() as holder:
        await holder.scalar(select(InvoiceLine).with_for_update())  # lock every line
        taps = [
            asyncio.create_task(
                bot.application.process_update(Update.de_json(tap, bot.application.bot))
            )
            for _ in range(2)
        ]
        await asyncio.sleep(0.3)
        assert not any(task.done() for task in taps)
    await asyncio.gather(*taps)

    assert bot.errors == []
    answers = [c.params.get("text") for c in bot.telegram.calls_to("answerCallbackQuery")]
    assert sorted(answers[-2:]) == sorted(["Marked as paid ✅", ALREADY_PAID])
    async with bot.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(Transfer)) == 1


async def test_payee_without_dms_confirms_on_the_card(bot: BotHarness) -> None:
    card = await october_card(bot, dms=())

    await card.tap(BOB_PAYS_ALICE, BOB)
    assert card.alert() == "Waiting for alice to confirm ⏳"
    assert bot.telegram.last("sendMessage").message["message_id"] == card.message["message_id"]

    await card.tap("☑️ bob → alice $12.50 received", ALICE)

    assert card.alert() == "Marked as paid ✅"
    assert f"✅ <s>{BOB_PAID}</s>" in card.current["text"]


async def test_dm_after_confirming_on_the_card(bot: BotHarness) -> None:
    card, asked = await bob_says_paid(bot)
    await card.tap("☑️ bob → alice $12.50 received", ALICE)

    await asked.tap("❌ Not received", ALICE)

    assert card.alert() == ALREADY_PAID
    assert (
        bot.telegram.last("editMessageReplyMarkup").params["message_id"]
        == (asked.message["message_id"])
    )
    async with bot.sessionmaker() as db:
        assert await db.scalar(select(Transfer)) is not None


async def test_payee_and_admin_can_mark_but_others_cannot(bot: BotHarness) -> None:
    card = await october_card(bot)

    bot.telegram.clear()

    await card.tap("✅ carol → alice $12.50 paid", STRANGER)
    assert card.alert() == "Only carol, alice or the admin can mark this."

    await card.tap("✅ carol → alice $12.50 paid", ALICE)  # alice is both the payee and the admin
    assert card.alert() == "Marked as paid ✅"
    assert "✅ <s>carol → @alice_handle $12.50</s>" in card.current["text"]
    assert all(
        call.params["chat_id"] != ALICE["id"] for call in bot.telegram.calls_to("sendMessage")
    )

    await card.tap("↩️ Undo carol → alice", ALICE)
    assert card.alert() == "Marked as unpaid"
    assert await audit_actions(bot) == ["invoice.paid", "invoice.unpaid"]


async def test_replaced_invoice_has_no_buttons(bot: BotHarness) -> None:
    card = await october_card(bot)
    stale = card.buttons()["✅ carol → alice $12.50 paid"]

    await invoice_for(bot, "2026-11", DEC_1)

    assert card.buttons() == {}
    await bot.send(callback_update(stale, sender=ALICE, on_message=card.message))
    assert card.alert() == REPLACED


async def test_a_pending_line_carries_over_like_an_unpaid_one(bot: BotHarness) -> None:
    card, asked = await bob_says_paid(bot)

    await invoice_for(bot, "2026-11", DEC_1)

    assert f"↪️ {BOB_PAID} (carried into November 2026)" in card.current["text"]
    november = bot.telegram.sent_texts(GROUP_ID)[-1]
    assert f"• {BOB_PAID}" in november
    await asked.tap("✅ Received", ALICE)
    assert card.alert() == REPLACED
    async with bot.sessionmaker() as db:
        assert await db.scalar(select(Transfer)) is None


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


async def test_balance_marks_pending_lines(bot: BotHarness) -> None:
    await bob_says_paid(bot)

    await bot.send(text_update("/balance", sender=BOB, chat_id=BOB["id"]))

    assert bot.telegram.last("sendMessage").params["text"].splitlines()[-1] == (
        "🧾 To settle: pay @alice_handle $12.50 (pending confirmation)"
    )
