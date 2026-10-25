from datetime import timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import SAWarning

from badminton.bot.handlers import shuttle as shuttle_handler
from badminton.bot.handlers.shuttle import ALREADY_CANCELLED, IN_USE
from badminton.bot.membership import NOT_A_MEMBER, OPEN_CHAT
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
from tests.fake_telegram import GROUP_ID, callback_update, text_update, user

USAGE = "Usage: /shuttle &lt;price&gt;, e.g. /shuttle 76.79"
EXPIRED = "This form has expired. Use /shuttle again."


async def test_shuttle_needs_a_price(bot: BotHarness) -> None:
    await bot.send(text_update("/shuttle", sender=ALICE, chat_id=ALICE["id"]))
    await bot.send(text_update("/shuttle abc", sender=BOB, chat_id=BOB["id"]))

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
    assert card.current["chat"]["id"] == BOB["id"]
    assert card.text == f"✅ Recorded shuttle purchase #{batch.id}, and posted it in the group."
    assert card.buttons() == {}
    announcement = bot.telegram.last("sendMessage")
    assert announcement.params["chat_id"] == GROUP_ID
    assert announcement.params["text"] == (
        f"🛍 <b>New shuttle batch #{batch.id}</b>: $76.79, paid by bob. "
        "It goes into use when the admin starts it with /shuttle_finished."
    )
    assert set(Card(bot, announcement.message, BOB).buttons()) == {"🗑 Cancel purchase"}
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
    announcement = await purchase(bot, "76.79")
    sent = len(bot.telegram.sent_texts())

    await bot.send(text_update("/shuttle 20", sender=CAROL, chat_id=CAROL["id"]))

    (waiting,) = await batches(bot)
    assert bot.telegram.sent_texts()[sent:] == [
        f"⚠️ Batch #{waiting.id} ($76.79, paid by bob) is already waiting to be used. "
        "Only one purchase can wait at a time, so record this one after the admin finishes "
        "the current batch with /shuttle_finished."
    ]
    assert set(announcement.buttons()) == {"🗑 Cancel purchase"}


async def test_a_purchase_recorded_while_the_form_was_open_wins(bot: BotHarness) -> None:
    """The database refuses the second waiting batch, as when two Record taps race."""
    late = await Card.open(bot, "/shuttle 20", CAROL)
    await purchase(bot, "76.79", BOB)

    await late.tap("✅ Record")

    (waiting,) = await batches(bot)
    assert waiting.amount_cents == 7679
    assert late.text.startswith(f"⚠️ Batch #{waiting.id} ($76.79, paid by bob) is already")


async def test_a_second_record_tap_keeps_the_confirmation(bot: BotHarness) -> None:
    """The second tap of a double-tap waits for the first, then finds the form gone."""
    card = await Card.open(bot, "/shuttle 76.79", BOB)
    record = callback_update(card.buttons()["✅ Record"], sender=BOB, on_message=card.message)
    await bot.send(record)
    edits = len(bot.telegram.calls_to("editMessageText"))

    await bot.send(record)

    (batch,) = await batches(bot)
    assert card.last_alert() == EXPIRED
    assert len(bot.telegram.calls_to("editMessageText")) == edits
    assert card.text == f"✅ Recorded shuttle purchase #{batch.id}, and posted it in the group."


async def test_a_double_tap_on_record_records_once(
    bot: BotHarness, recwarn: pytest.WarningsRecorder
) -> None:
    card = await Card.open(bot, "/shuttle 76.79", BOB)

    await bot.double_tap(
        callback_update(card.buttons()["✅ Record"], sender=BOB, on_message=card.message)
    )

    (batch,) = await batches(bot)
    answers = [call.params.get("text") for call in bot.telegram.calls_to("answerCallbackQuery")]
    assert sorted(answers, key=str) == [None, EXPIRED]  # one went through
    assert card.text == f"✅ Recorded shuttle purchase #{batch.id}, and posted it in the group."
    assert len(bot.telegram.sent_texts(GROUP_ID)) == 1  # announced once
    assert not any("already waiting" in text for text in bot.telegram.sent_texts())
    assert not [w for w in recwarn if issubclass(w.category, SAWarning)]


@pytest.mark.parametrize("sender", [BOB, ALICE])
async def test_shuttle_in_the_group_points_to_the_private_chat(
    bot: BotHarness, sender: dict
) -> None:
    await bot.send(text_update("/shuttle 76.79", sender=sender))

    reply = bot.telegram.last("sendMessage").params
    assert (reply["chat_id"], reply["text"]) == (
        GROUP_ID,
        "🔒 Send /shuttle to me in a private chat.",
    )
    assert reply["reply_markup"]["inline_keyboard"] == [
        [{"text": OPEN_CHAT, "url": "https://t.me/badminton_bot"}]
    ]
    async with bot.sessionmaker() as db:
        assert await db.scalar(select(Draft)) is None


async def test_outsiders_cannot_record_a_purchase(bot: BotHarness) -> None:
    stranger = user(999, "stranger")  # not in the group, but anyone can DM a bot
    bot.telegram.set_member_status(stranger["id"], "left")

    await bot.send(text_update("/shuttle 999", sender=stranger, chat_id=stranger["id"]))

    assert bot.telegram.sent_texts() == [NOT_A_MEMBER]
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


async def test_buyer_cancels_their_purchase_from_its_announcement(bot: BotHarness) -> None:
    card = await purchase(bot, "76.79")
    assert card.current["chat"]["id"] == GROUP_ID
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
