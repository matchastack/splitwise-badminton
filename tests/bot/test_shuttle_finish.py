from datetime import timedelta

from sqlalchemy import select

from badminton.bot.admin import NOT_ADMIN
from badminton.bot.handlers.shuttle_finish import NOTHING_WAITING
from badminton.db.models import AuditLog, Session
from badminton.domain.timeutil import fmt_date, sgt_date, utc_now
from badminton.services.repo import load_ledger
from tests.bot.attendance_helpers import make_session
from tests.bot.shuttle_helpers import (
    SHAWN,
    ZY,
    Card,
    add_batch,
    batches,
    played,
    purchase,
    seed_members,
)
from tests.conftest import BotHarness
from tests.fake_telegram import GROUP_ID, text_update


async def finish(bot: BotHarness, start: str = "From the next session on") -> Card:
    card = await Card.open(bot, "/shuttle_finished")
    await card.tap_starting(start)
    await card.tap_starting("✅")
    return card


async def test_finishing_needs_an_admin_and_a_waiting_batch(bot: BotHarness) -> None:
    await bot.send(text_update("/shuttle_finished", sender=SHAWN))
    await bot.send(text_update("/shuttle_finished", sender=ZY))

    assert bot.telegram.sent_texts() == [NOT_ADMIN, NOTHING_WAITING]


async def test_finishing_puts_the_waiting_batch_in_use_and_shows_the_split(
    bot: BotHarness,
) -> None:
    ids = await seed_members(bot, "zy2", "shawn2")
    old = await add_batch(bot, ids["zy2"], 8000, starts_at=utc_now() - timedelta(days=30))
    await played(bot, 300, [ids["zy2"], ids["shawn2"]])
    await played(bot, 100, [ids["zy2"]], minutes=60)
    await purchase(bot, "76.79")
    new = (await batches(bot))[-1].id

    card = await Card.open(bot, "/shuttle_finished")
    assert card.text.splitlines() == [
        f"🏁 <b>Finish shuttle batch #{old}?</b>",
        f"Batch #{new} ($76.79, paid by shawn) takes over.",
        f"Which session is the first to use batch #{new}?",
    ]
    await card.tap("From the next session on")
    assert "Used from: the next session" in card.text
    assert set(card.buttons()) == {f"✅ Finish batch #{old}", "‹ Change", "✖ Cancel"}
    await card.tap(f"✅ Finish batch #{old}")

    assert card.text == f"✅ Batch #{old} is finished; batch #{new} is in use."
    assert bot.telegram.sent_texts(GROUP_ID)[-1].splitlines() == [
        f"🏁 <b>Batch #{old} is finished</b>: $80.00 over 5h = $16.00/h",
        "• zy2 3h — $48.00",
        "• shawn2 2h — $32.00",
        "These amounts go on the next monthly invoice.",
        "",
        f"🏸 Batch #{new} ($76.79, paid by shawn) is in use from the next session.",
    ]
    batch = (await batches(bot))[-1]
    assert not batch.waiting
    assert batch.starts_at == batch.opened_at <= utc_now()
    async with bot.sessionmaker() as db:
        ledger = await load_ledger(db, utc_now())
        audit = await db.scalar(select(AuditLog).where(AuditLog.action == "shuttle.finish"))
    assert ledger.open_batch_id == new
    assert audit.payload["before"] == {"in_use": old}


async def test_split_counts_a_session_in_progress(bot: BotHarness) -> None:
    ids = await seed_members(bot, "zy2", "yt", "hw")
    old = await add_batch(bot, ids["zy2"], 8000, starts_at=utc_now() - timedelta(days=30))
    await played(bot, 1, [ids["yt"], ids["hw"]])  # 2h, started an hour ago
    await purchase(bot, "76.79")

    await finish(bot)

    assert bot.telegram.sent_texts(GROUP_ID)[-1].splitlines()[:4] == [
        f"🏁 <b>Batch #{old} is finished</b>: $80.00 over 4h = $20.00/h",
        "• yt 2h — $40.00",
        "• hw 2h — $40.00",
        "These amounts go on the next monthly invoice.",
    ]


async def test_last_session_option_moves_it_to_the_new_batch(bot: BotHarness) -> None:
    ids = await seed_members(bot, "yt")
    old = await add_batch(bot, ids["yt"], 5000, starts_at=utc_now() - timedelta(days=30))
    last = await played(bot, 5, [ids["yt"]])
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
        f"🏁 <b>Batch #{old} is finished</b>: nobody played with it, so it stays with yt."
    )


async def test_the_first_batch_starts_with_nothing_in_use(bot: BotHarness) -> None:
    upcoming = utc_now() + timedelta(days=4)
    await make_session(bot, starts_at=upcoming, payer_telegram_id=None)
    await purchase(bot, "76.79")
    (batch,) = await batches(bot)

    card = await Card.open(bot, "/shuttle_finished")
    assert card.text.splitlines()[0] == (
        f"🏸 <b>Start shuttle batch #{batch.id} ($76.79, paid by shawn)?</b>"
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
        f"🏸 Batch #{batch.id} ($76.79, paid by shawn) is in use from the next session."
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
