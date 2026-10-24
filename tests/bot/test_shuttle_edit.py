from datetime import timedelta

import pytest
from sqlalchemy import select

from badminton.bot.admin import NOT_ADMIN
from badminton.bot.handlers import shuttle_edit as shuttle_edit_handler
from badminton.db.models import AuditLog
from badminton.domain.ledger import EntryKind
from badminton.domain.timeutil import fmt_date, sgt_date, utc_now
from badminton.services.repo import load_ledger
from tests.bot.shuttle_helpers import (
    ALICE,
    BOB,
    CAROL,
    Card,
    add_batch,
    balances,
    batches,
    played,
    purchase,
    removed_first,
    seed_members,
)
from tests.bot.test_shuttle_finish import finish
from tests.conftest import BotHarness
from tests.fake_telegram import (
    GROUP_ID,
    callback_update,
    message,
    message_update,
    text_update,
)


async def audit_actions(bot: BotHarness) -> list[str]:
    async with bot.sessionmaker() as db:
        return list(await db.scalars(select(AuditLog.action).order_by(AuditLog.id)))


async def open_batch(bot: BotHarness, batch_id: int) -> Card:
    card = await Card.open(bot, "/shuttle_edit")
    await card.tap_starting(f"#{batch_id} ·")
    return card


# Who can cancel a waiting purchase ----------------------------------------------


async def test_other_members_cannot_cancel_or_remove_a_purchase(bot: BotHarness) -> None:
    receipt = await purchase(bot, "76.79", BOB)
    (batch,) = await batches(bot)
    editor = await Card.open(bot, "/shuttle_edit", ALICE)
    announced = len(bot.telegram.sent_texts(GROUP_ID))

    # From the purchase's own card...
    await receipt.tap("🗑 Cancel purchase", sender=CAROL)
    assert receipt.last_alert() == "Only bob or the admin can cancel this purchase."
    assert set(receipt.buttons()) == {"🗑 Cancel purchase"}
    # ...from /shuttle_edit...
    await bot.send(text_update("/shuttle_edit", sender=CAROL))
    assert bot.telegram.sent_texts()[-1] == NOT_ADMIN
    # ...or from the admin's open /shuttle_edit card.
    await editor.tap_starting(f"#{batch.id} ·", sender=CAROL)
    assert editor.last_alert() == NOT_ADMIN

    (still,) = await batches(bot)
    assert still.waiting
    assert await audit_actions(bot) == ["shuttle.add"]
    # Nothing was announced: the only new message in the group is the refusal.
    assert bot.telegram.sent_texts(GROUP_ID)[announced:] == [NOT_ADMIN]

    # The payer still can.
    await receipt.tap("🗑 Cancel purchase", sender=BOB)
    await receipt.tap("Yes, cancel it", sender=BOB)
    assert await batches(bot) == []


async def test_the_admin_cancels_a_members_purchase_from_its_card(bot: BotHarness) -> None:
    receipt = await purchase(bot, "76.79", BOB)

    await receipt.tap("🗑 Cancel purchase", sender=ALICE)
    await receipt.tap("Yes, cancel it", sender=ALICE)

    assert await batches(bot) == []
    assert receipt.text.endswith("Cancelled by alice.")
    assert await audit_actions(bot) == ["shuttle.add", "shuttle.cancel"]


async def test_the_admin_removes_a_waiting_batch(bot: BotHarness) -> None:
    receipt = await purchase(bot, "76.79", BOB)
    (batch,) = await batches(bot)

    card = await Card.open(bot, "/shuttle_edit")
    assert set(card.buttons()) == {f"#{batch.id} · waiting · $76.79 · bob", "✖ Close"}
    await card.tap(f"#{batch.id} · waiting · $76.79 · bob")
    assert card.text.splitlines()[1] == "Waiting to be used."
    assert set(card.buttons()) == {"💵 Price", "👤 Payer", "🗑 Remove", "‹ Batches", "✖ Close"}
    await card.tap("🗑 Remove")
    await card.tap("Yes, remove it")

    assert await batches(bot) == []
    assert card.text == f"🗑 Removed shuttle batch #{batch.id}."
    assert bot.telegram.sent_texts(GROUP_ID)[-1] == (
        f"🗑 Admin removed shuttle batch #{batch.id} ($76.79, paid by bob). "
        "It wasn't in use yet, so nobody is charged for it."
    )
    assert await audit_actions(bot) == ["shuttle.add", "shuttle.delete"]
    # The purchase's own cancel button is stale now.
    await receipt.tap("🗑 Cancel purchase")
    assert receipt.last_alert() == "This purchase is already cancelled."
    assert receipt.buttons() == {}


async def test_a_second_remove_tap_keeps_the_result(bot: BotHarness) -> None:
    """The second tap of a double-tap waits for the first, then finds the form gone."""
    await purchase(bot, "76.79", BOB)
    (batch,) = await batches(bot)
    card = await open_batch(bot, batch.id)
    await card.tap("🗑 Remove")
    remove = callback_update(
        card.buttons()["Yes, remove it"], sender=ALICE, on_message=card.message
    )
    await bot.send(remove)
    sent = len(bot.telegram.sent_texts(GROUP_ID))

    await bot.send(remove)

    assert card.last_alert() == "This form has expired. Use /shuttle_edit again."
    assert card.text == f"🗑 Removed shuttle batch #{batch.id}."
    assert len(bot.telegram.sent_texts(GROUP_ID)) == sent
    assert await audit_actions(bot) == ["shuttle.add", "shuttle.delete"]


async def test_removing_a_purchase_its_buyer_just_cancelled(
    bot: BotHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await purchase(bot, "76.79", BOB)
    (batch,) = await batches(bot)
    card = await open_batch(bot, batch.id)
    await card.tap("🗑 Remove")
    removed_first(bot, monkeypatch, shuttle_edit_handler)  # the buyer's cancel landed first

    await card.tap("Yes, remove it")

    assert card.last_alert() == f"Batch #{batch.id} no longer exists."
    assert card.text == "There are no shuttle batches to edit."
    assert await audit_actions(bot) == ["shuttle.add"]  # nothing removed twice


# Price and payer -----------------------------------------------------------------


async def test_new_price_on_a_finished_batch_resplits_it(bot: BotHarness) -> None:
    ids = await seed_members(bot, "alice2", "bob2")
    started = utc_now() - timedelta(days=30)
    old = await add_batch(bot, ids["alice2"], 8000, starts_at=started)
    await played(bot, 300, [ids["alice2"], ids["bob2"]])
    await played(bot, 100, [ids["alice2"]], minutes=60)
    ended = utc_now() - timedelta(hours=50)
    current = await add_batch(bot, ids["bob2"], 7000, starts_at=ended)
    before = await balances(bot)

    card = await Card.open(bot, "/shuttle_edit")
    assert set(card.buttons()) == {
        f"#{current} · in use · $70.00 · bob2",
        f"#{old} · finished · $80.00 · alice2",
        "✖ Close",
    }
    await card.tap(f"#{old} · finished · $80.00 · alice2")
    assert card.text.splitlines()[1] == (
        f"Used from {fmt_date(sgt_date(started))} until {fmt_date(sgt_date(ended))}."
    )
    assert set(card.buttons()) == {"💵 Price", "👤 Payer", "‹ Batches", "✖ Close"}
    await card.tap("💵 Price")
    prompt = bot.telegram.last("sendMessage").message
    await bot.send(message_update(message(sender=ALICE, text="60", reply_to=prompt)))

    assert bot.telegram.sent_texts(GROUP_ID)[-1].splitlines() == [
        f"✏️ Admin changed shuttle batch #{old}: price $80.00 → $60.00",
        f"Recalculated batch #{old}: $60.00 over 5h = $12.00/h",
        "• alice2 3h — $36.00",
        "• bob2 2h — $24.00",
        "Any difference shows as an adjustment on the next monthly statement.",
    ]
    after = await balances(bot)
    assert after[ids["bob2"]] - before[ids["bob2"]] == 800  # $32.00 → $24.00
    assert after[ids["alice2"]] - before[ids["alice2"]] == -800  # credited $20 less, owes $12 less
    assert sum(after.values()) == 0
    assert "What do you want to change?" in card.text
    async with bot.sessionmaker() as db:
        audit = await db.scalar(select(AuditLog).where(AuditLog.action == "shuttle.edit"))
    assert (audit.payload["before"]["amount_cents"], audit.payload["after"]["amount_cents"]) == (
        8000,
        6000,
    )


async def test_new_payer_on_a_waiting_batch(bot: BotHarness) -> None:
    ids = await seed_members(bot, "frank")
    await purchase(bot, "76.79", BOB)
    (batch,) = await batches(bot)

    card = await open_batch(bot, batch.id)
    await card.tap("👤 Payer")
    await card.tap("frank")

    assert (await batches(bot))[0].payer_member_id == ids["frank"]
    assert bot.telegram.sent_texts(GROUP_ID)[-1] == (
        f"✏️ Admin changed shuttle batch #{batch.id}: paid by bob → frank"
    )


async def test_typing_the_price_it_already_has_changes_nothing(bot: BotHarness) -> None:
    await purchase(bot, "76.79", BOB)
    (batch,) = await batches(bot)
    card = await open_batch(bot, batch.id)
    await card.tap("💵 Price")
    prompt = bot.telegram.last("sendMessage").message
    edits = len(bot.telegram.calls_to("editMessageText"))
    announced = len(bot.telegram.sent_texts(GROUP_ID))
    # Telegram refuses an edit that changes nothing, so the card must be left alone.
    bot.telegram.fail("editMessageText", 400, "Bad Request: message is not modified")

    await bot.send(message_update(message(sender=ALICE, text="76.79", reply_to=prompt)))

    assert bot.telegram.calls_to("editMessageText")[edits:] == []
    assert len(bot.telegram.sent_texts(GROUP_ID)) == announced
    assert prompt["message_id"] in [
        c.params["message_id"] for c in bot.telegram.calls_to("deleteMessage")
    ]


async def test_a_price_prompt_goes_with_a_cancelled_batch(bot: BotHarness) -> None:
    receipt = await purchase(bot, "76.79", BOB)
    (batch,) = await batches(bot)
    card = await open_batch(bot, batch.id)
    await card.tap("💵 Price")
    first_prompt = bot.telegram.last("sendMessage").message
    await receipt.tap("🗑 Cancel purchase")
    await receipt.tap("Yes, cancel it")
    await card.tap("💵 Price")  # the batch is gone: back to the list

    await bot.send(message_update(message(sender=ALICE, text="50", reply_to=first_prompt)))

    assert card.text == "There are no shuttle batches to edit."
    assert first_prompt["message_id"] in [
        c.params["message_id"] for c in bot.telegram.calls_to("deleteMessage")
    ]


# Undoing /shuttle_finished ------------------------------------------------------


async def test_undo_finish_reopens_the_previous_batch(bot: BotHarness) -> None:
    ids = await seed_members(bot, "alice2")
    old = await add_batch(bot, ids["alice2"], 8000, starts_at=utc_now() - timedelta(days=30))
    await played(bot, 300, [ids["alice2"]])
    await purchase(bot, "76.79", BOB)
    await finish(bot)
    new = (await batches(bot))[-1].id

    card = await open_batch(bot, new)
    assert set(card.buttons()) == {
        "💵 Price",
        "👤 Payer",
        "↩️ Undo /shuttle_finished",
        "‹ Batches",
        "✖ Close",
    }
    await card.tap("↩️ Undo /shuttle_finished")
    assert card.text.endswith(
        f"Put this batch back to waiting? Batch #{old} will be in use again, "
        "and its split is withdrawn."
    )
    await card.tap("Yes, put it back")

    reopened = (await batches(bot))[-1]
    assert reopened.waiting and reopened.starts_at is None
    async with bot.sessionmaker() as db:
        ledger = await load_ledger(db, utc_now())
    assert ledger.open_batch_id == old
    assert not [e for e in ledger.entries if e.kind is EntryKind.SHUTTLE_PAID]
    assert card.text == f"↩️ Batch #{new} is waiting again."
    assert bot.telegram.sent_texts(GROUP_ID)[-1] == (
        f"↩️ Admin undid finishing batch #{old}: it's in use again, and its split is "
        f"withdrawn. Batch #{new} ($76.79, paid by bob) is waiting again."
    )
    assert (await audit_actions(bot))[-1] == "shuttle.reopen"


async def test_undo_is_refused_while_another_batch_waits(bot: BotHarness) -> None:
    ids = await seed_members(bot, "alice2")
    await add_batch(bot, ids["alice2"], 8000, starts_at=utc_now() - timedelta(days=30))
    await purchase(bot, "76.79", BOB)
    await finish(bot)
    in_use = (await batches(bot))[-1].id
    await purchase(bot, "20", CAROL)
    waiting = (await batches(bot))[-1].id

    card = await open_batch(bot, in_use)
    await card.tap("↩️ Undo /shuttle_finished")

    assert card.last_alert() == (
        f"Batch #{waiting} is waiting, and only one batch can wait. Remove it first."
    )
    assert not next(b for b in await batches(bot) if b.id == in_use).waiting
