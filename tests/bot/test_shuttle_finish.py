from datetime import timedelta

from sqlalchemy import select

from badminton.bot.admin import NOT_ADMIN
from badminton.bot.handlers.shuttle_finish import NOTHING_WAITING
from badminton.db.models import AuditLog, Session
from badminton.domain.timeutil import fmt_date, sgt_date, utc_now
from badminton.services.repo import load_ledger
from tests.bot.attendance_helpers import make_session
from tests.bot.shuttle_helpers import (
    ALICE,
    BOB,
    Card,
    add_batch,
    batches,
    played,
    purchase,
    seed_members,
)
from tests.conftest import BotHarness
from tests.fake_telegram import GROUP_ID, callback_update, text_update


async def finish(bot: BotHarness, start: str = "From the next session on") -> Card:
    card = await Card.open(bot, "/shuttle_finished")
    await card.tap_starting(start)
    await card.tap_starting("✅")
    return card


async def test_finishing_needs_an_admin_and_a_waiting_batch(bot: BotHarness) -> None:
    await bot.send(text_update("/shuttle_finished", sender=BOB, chat_id=BOB["id"]))
    await bot.send(text_update("/shuttle_finished", sender=ALICE, chat_id=ALICE["id"]))

    assert bot.telegram.sent_texts() == [NOT_ADMIN, NOTHING_WAITING]


async def test_finishing_puts_the_waiting_batch_in_use_and_shows_the_split(
    bot: BotHarness,
) -> None:
    ids = await seed_members(bot, "alice2", "bob2")
    old = await add_batch(bot, ids["alice2"], 8000, starts_at=utc_now() - timedelta(days=30))
    await played(bot, 300, [ids["alice2"], ids["bob2"]])
    await played(bot, 100, [ids["alice2"]], minutes=60)
    await purchase(bot, "76.79")
    new = (await batches(bot))[-1].id

    card = await Card.open(bot, "/shuttle_finished")
    assert card.text.splitlines() == [
        f"🏁 <b>Finish shuttle batch #{old}?</b>",
        f"Batch #{new} ($76.79, paid by bob) takes over.",
        f"Which session is the first to use batch #{new}?",
    ]
    await card.tap("From the next session on")
    assert "Used from: the next session" in card.text
    assert set(card.buttons()) == {f"✅ Finish batch #{old}", "‹ Change", "✖ Cancel"}
    await card.tap(f"✅ Finish batch #{old}")

    assert card.text == f"✅ Batch #{old} is finished; batch #{new} is in use."
    assert bot.telegram.sent_texts(GROUP_ID)[-1].splitlines() == [
        f"🏁 <b>Batch #{old} is finished</b>: $80.00 over 5h = $16.00/h",
        "• alice2 3h — $48.00",
        "• bob2 2h — $32.00",
        "These amounts go on the next monthly invoice.",
        "",
        f"🏸 Batch #{new} ($76.79, paid by bob) is in use from the next session.",
    ]
    batch = (await batches(bot))[-1]
    assert not batch.waiting
    assert batch.starts_at == batch.opened_at <= utc_now()
    async with bot.sessionmaker() as db:
        ledger = await load_ledger(db, utc_now())
        audit = await db.scalar(select(AuditLog).where(AuditLog.action == "shuttle.finish"))
    assert ledger.open_batch_id == new
    assert audit.payload["before"] == {"in_use": old}


async def test_a_second_finish_tap_keeps_the_result(bot: BotHarness) -> None:
    """The second tap of a double-tap waits for the first, then finds the form gone."""
    await purchase(bot, "76.79")
    card = await Card.open(bot, "/shuttle_finished")
    await card.tap("From the next session on")
    finish_tap = callback_update(
        card.buttons()["✅ Start batch #1"], sender=ALICE, on_message=card.message
    )
    await bot.send(finish_tap)
    edits = len(bot.telegram.calls_to("editMessageText"))

    await bot.send(finish_tap)

    assert card.last_alert() == "This form has expired. Use /shuttle_finished again."
    assert len(bot.telegram.calls_to("editMessageText")) == edits
    assert card.text == "✅ Batch #1 is in use."
    async with bot.sessionmaker() as db:
        finished = await db.scalars(select(AuditLog).where(AuditLog.action == "shuttle.finish"))
        assert len(finished.all()) == 1


async def test_split_counts_a_session_in_progress(bot: BotHarness) -> None:
    ids = await seed_members(bot, "alice2", "frank", "dave")
    old = await add_batch(bot, ids["alice2"], 8000, starts_at=utc_now() - timedelta(days=30))
    await played(bot, 1, [ids["frank"], ids["dave"]])  # 2h, started an hour ago
    await purchase(bot, "76.79")

    await finish(bot)

    assert bot.telegram.sent_texts(GROUP_ID)[-1].splitlines()[:4] == [
        f"🏁 <b>Batch #{old} is finished</b>: $80.00 over 4h = $20.00/h",
        "• frank 2h — $40.00",
        "• dave 2h — $40.00",
        "These amounts go on the next monthly invoice.",
    ]


async def test_last_session_option_moves_it_to_the_new_batch(bot: BotHarness) -> None:
    ids = await seed_members(bot, "frank")
    old = await add_batch(bot, ids["frank"], 5000, starts_at=utc_now() - timedelta(days=30))
    last = await played(bot, 5, [ids["frank"]])
    await purchase(bot, "30")

    card = await Card.open(bot, "/shuttle_finished")
    await card.tap_starting("Last session")
    assert "(the last session)" in card.text
    await card.tap(f"✅ Finish batch #{old}")

    batch = (await batches(bot))[-1]
    async with bot.sessionmaker() as db:
        session = await db.get(Session, last)
    assert batch.starts_at == session.starts_at
    # That was the only session, so the old batch was never played with.
    assert bot.telegram.sent_texts(GROUP_ID)[-1].splitlines()[0] == (
        f"🏁 <b>Batch #{old} is finished</b>: nobody played with it, so it stays with frank."
    )


async def test_the_first_batch_starts_with_nothing_in_use(bot: BotHarness) -> None:
    upcoming = utc_now() + timedelta(days=4)
    await make_session(bot, starts_at=upcoming, payer_telegram_id=None)
    await purchase(bot, "76.79")
    (batch,) = await batches(bot)

    card = await Card.open(bot, "/shuttle_finished")
    assert card.text.splitlines()[0] == (
        f"🏸 <b>Start shuttle batch #{batch.id} ($76.79, paid by bob)?</b>"
    )
    next_label = f"Next session ({fmt_date(sgt_date(upcoming))})"
    assert set(card.buttons()) == {next_label, "✖ Cancel"}
    await card.tap(next_label)
    await card.tap("‹ Change")
    assert set(card.buttons()) == {next_label, "✖ Cancel"}
    await card.tap(next_label)
    await card.tap(f"✅ Start batch #{batch.id}")

    assert card.text == f"✅ Batch #{batch.id} is in use."
    assert bot.telegram.sent_texts(GROUP_ID)[-1] == (
        f"🏸 Batch #{batch.id} ($76.79, paid by bob) is in use from the next session."
    )
    assert not (await batches(bot))[0].waiting


async def test_a_purchase_cancelled_meanwhile_is_not_put_in_use(bot: BotHarness) -> None:
    receipt = await purchase(bot, "76.79")
    (batch,) = await batches(bot)
    card = await Card.open(bot, "/shuttle_finished")
    await receipt.tap("🗑 Cancel purchase")
    await receipt.tap("Yes, cancel it")

    await card.tap("From the next session on")

    message = f"Batch #{batch.id} is no longer waiting, so nothing changed."
    assert card.last_alert() == message
    assert card.text == f"✖ {message}"
    assert await batches(bot) == []


async def test_cancel_leaves_the_batches_alone(bot: BotHarness) -> None:
    await purchase(bot, "76.79")
    card = await Card.open(bot, "/shuttle_finished")

    await card.tap("✖ Cancel")

    assert card.text == "✖ No batch was finished."
    assert (await batches(bot))[0].waiting
