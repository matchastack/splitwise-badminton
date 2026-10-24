from datetime import timedelta

import pytest
from sqlalchemy import select, update

from badminton.bot.handlers import shuttle as shuttle_handler
from badminton.bot.handlers.shuttle import ALREADY_CANCELLED, IN_THE_GROUP, IN_USE
from badminton.db.models import AuditLog, Draft, ShuttleBatch
from badminton.domain.timeutil import utc_now
from tests.bot.shuttle_helpers import (
    ALICE,
    BOB,
    CAROL,
    Card,
    add_batch,
    balances,
    batches,
    member_id,
    played,
    purchase,
    removed_first,
    seed_members,
)
from tests.conftest import BotHarness
from tests.fake_telegram import GROUP_ID, text_update, user

USAGE = "Usage: /shuttle &lt;price&gt;, e.g. /shuttle 76.79"


async def test_shuttle_needs_a_price(bot: BotHarness) -> None:
    await bot.send(text_update("/shuttle", sender=ALICE))
    await bot.send(text_update("/shuttle abc", sender=BOB))

    assert bot.telegram.sent_texts() == [USAGE, USAGE]


async def test_member_records_a_purchase_they_paid_for(bot: BotHarness) -> None:
    card = await Card.open(bot, "/shuttle 76.79", BOB)
    assert card.text == (
        "🏸 <b>New shuttle purchase</b>: $76.79\nPaid by bob\n"
        "It goes into use when the admin starts it with /shuttle_finished."
    )
    assert set(card.buttons()) == {"✅ Record", "✖ Cancel"}

    await card.tap("✅ Record")

    (batch,) = await batches(bot)
    bob = await member_id(bot, BOB)
    assert (batch.payer_member_id, batch.amount_cents) == (bob, 7679)
    assert batch.waiting and batch.starts_at is None
    assert card.text == f"✅ Recorded shuttle purchase #{batch.id}."
    assert set(card.buttons()) == {"🗑 Cancel purchase"}
    assert bot.telegram.sent_texts(GROUP_ID)[-1] == (
        f"🛍 <b>New shuttle batch #{batch.id}</b>: $76.79, paid by bob. "
        "It goes into use when the admin starts it with /shuttle_finished."
    )
    async with bot.sessionmaker() as db:
        audit = await db.scalar(select(AuditLog))
    assert (audit.action, audit.actor_member_id) == ("shuttle.add", bob)


async def test_admin_picks_the_payer(bot: BotHarness) -> None:
    ids = await seed_members(bot, "frank", "dave")

    card = await Card.open(bot, "/shuttle 30", ALICE)
    assert "Who paid?" in card.text
    await card.tap("frank")
    assert "Paid by frank" in card.text
    await card.tap("✅ Record")

    (batch,) = await batches(bot)
    assert (batch.payer_member_id, batch.amount_cents) == (ids["frank"], 3000)


async def test_a_purchase_leaves_the_batch_in_use_alone(bot: BotHarness) -> None:
    ids = await seed_members(bot, "alice2", "frank")
    in_use = await add_batch(bot, ids["alice2"], 8000, starts_at=utc_now() - timedelta(days=30))
    await played(bot, 300, [ids["alice2"], ids["frank"]])
    before = await balances(bot)

    await purchase(bot, "76.79")

    assert await balances(bot) == before  # nothing was closed or billed
    assert bot.telegram.sent_texts(GROUP_ID)[-1].endswith(
        f"It goes into use when the admin finishes batch #{in_use}."
    )


async def test_only_one_purchase_can_wait(bot: BotHarness) -> None:
    card = await purchase(bot, "76.79")
    sent = len(bot.telegram.sent_texts())

    await bot.send(text_update("/shuttle 20", sender=CAROL))

    (waiting,) = await batches(bot)
    assert bot.telegram.sent_texts()[sent:] == [
        f"⚠️ Batch #{waiting.id} ($76.79, paid by bob) is already waiting to be used. "
        "Only one purchase can wait at a time, so record this one after the admin finishes "
        "the current batch with /shuttle_finished."
    ]
    assert card.text == f"✅ Recorded shuttle purchase #{waiting.id}."


async def test_a_purchase_recorded_while_the_form_was_open_wins(bot: BotHarness) -> None:
    """The database refuses the second waiting batch, as when two Record taps race."""
    late = await Card.open(bot, "/shuttle 20", CAROL)
    await purchase(bot, "76.79", BOB)

    await late.tap("✅ Record")

    (waiting,) = await batches(bot)
    assert waiting.amount_cents == 7679
    assert late.text.startswith(f"⚠️ Batch #{waiting.id} ($76.79, paid by bob) is already")


async def test_members_record_in_the_group_only(bot: BotHarness) -> None:
    stranger = user(999, "stranger")  # not in the group, but anyone can DM a bot
    await bot.send(text_update("/shuttle 999", sender=stranger, chat_id=stranger["id"]))
    await bot.send(text_update("/shuttle 76.79", sender=BOB, chat_id=BOB["id"]))

    assert bot.telegram.sent_texts() == [IN_THE_GROUP, IN_THE_GROUP]
    async with bot.sessionmaker() as db:
        assert await db.scalar(select(Draft)) is None


async def test_the_admin_records_by_dm(bot: BotHarness) -> None:
    await seed_members(bot, "frank")
    await bot.send(text_update("/shuttle 30", sender=ALICE, chat_id=ALICE["id"]))
    card = Card(bot, bot.telegram.last("sendMessage").message, ALICE)

    await card.tap("frank")
    await card.tap("✅ Record")

    assert len(await batches(bot)) == 1


async def test_only_the_sender_can_fill_it_in(bot: BotHarness) -> None:
    card = await Card.open(bot, "/shuttle 30", BOB)

    await card.tap("✅ Record", sender=CAROL)

    assert card.last_alert() == "Only bob can fill this in."
    assert await batches(bot) == []


async def test_cancel_the_form(bot: BotHarness) -> None:
    card = await Card.open(bot, "/shuttle 30", BOB)

    await card.tap("✖ Cancel")

    assert card.text == "✖ Shuttle purchase not recorded."
    assert await batches(bot) == []


async def test_buyer_cancels_their_purchase(bot: BotHarness) -> None:
    card = await purchase(bot, "76.79")
    (batch,) = await batches(bot)

    await card.tap("🗑 Cancel purchase")
    assert set(card.buttons()) == {"Yes, cancel it", "Keep it"}
    await card.tap("Keep it")
    assert set(card.buttons()) == {"🗑 Cancel purchase"}
    await card.tap("🗑 Cancel purchase")
    await card.tap("Yes, cancel it")

    assert await batches(bot) == []
    assert card.text == (
        f"🗑 <s>Shuttle purchase #{batch.id} ($76.79, paid by bob)</s>\nCancelled by bob."
    )
    assert bot.telegram.sent_texts(GROUP_ID)[-1] == (
        f"🗑 bob cancelled shuttle purchase #{batch.id} ($76.79, paid by bob). "
        "It wasn't in use yet, so nobody is charged for it."
    )
    async with bot.sessionmaker() as db:
        actions = list(await db.scalars(select(AuditLog.action).order_by(AuditLog.id)))
    assert actions == ["shuttle.add", "shuttle.cancel"]


async def test_a_purchase_in_use_cannot_be_cancelled(bot: BotHarness) -> None:
    card = await purchase(bot, "76.79")
    (batch,) = await batches(bot)
    async with bot.sessionmaker() as db:
        now = utc_now()
        await db.execute(
            update(ShuttleBatch)
            .where(ShuttleBatch.id == batch.id)
            .values(starts_at=now, opened_at=now)
        )
        await db.commit()

    await card.tap("🗑 Cancel purchase")

    assert card.last_alert() == IN_USE.format(batch_id=batch.id)
    assert card.buttons() == {}
    assert len(await batches(bot)) == 1


async def test_a_second_cancel_tap_says_already_cancelled(
    bot: BotHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt = await purchase(bot, "76.79")
    await receipt.tap("🗑 Cancel purchase")
    removed_first(bot, monkeypatch, shuttle_handler)  # the other tap got there first

    await receipt.tap("Yes, cancel it")

    assert receipt.last_alert() == ALREADY_CANCELLED
    assert await batches(bot) == []
