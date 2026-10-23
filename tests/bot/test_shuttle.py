from datetime import timedelta

from sqlalchemy import select

from badminton.bot.admin import NOT_ADMIN
from badminton.db.models import AuditLog, Member, Session, ShuttleBatch
from badminton.domain.timeutil import fmt_date, sgt_date, utc_now
from badminton.services.attendance import set_attendance
from tests.bot.attendance_helpers import make_session
from tests.conftest import BotHarness
from tests.fake_telegram import ADMIN_ID, GROUP_ID, callback_update, text_update, user

ALICE = user(ADMIN_ID, "alice", "alice_handle")
BOB = user(222, "bob")


class Card:
    def __init__(self, bot: BotHarness) -> None:
        self.bot = bot
        self.message = bot.telegram.last("sendMessage").message

    def latest(self) -> dict:
        for call in reversed(self.bot.telegram.calls):
            if (
                call.method == "editMessageText"
                and call.params["message_id"] == self.message["message_id"]
            ):
                return call.result
        return self.message

    def buttons(self) -> dict[str, str]:
        rows = self.latest().get("reply_markup", {}).get("inline_keyboard", [])
        return {b["text"]: b["callback_data"] for row in rows for b in row}

    async def tap(self, label: str, sender: dict = ALICE) -> None:
        data = self.buttons()[label]
        await self.bot.send(callback_update(data, sender=sender, on_message=self.message))


async def seed_members(bot: BotHarness, *names: str) -> dict[str, int]:
    async with bot.sessionmaker() as db:
        members = [Member(display_name=name) for name in names]
        db.add_all(members)
        await db.commit()
        return {m.display_name: m.id for m in members}


async def played(bot: BotHarness, hours_ago: int, who: list[int], minutes: int = 120) -> int:
    session_id = await make_session(
        bot,
        starts_at=utc_now() - timedelta(hours=hours_ago),
        minutes=minutes,
        payer_name=f"payer{hours_ago}",
        payer_telegram_id=None,
    )
    async with bot.sessionmaker() as db:
        for member_id in who:
            await set_attendance(db, session_id, member_id, "in")
        await db.commit()
    return session_id


async def test_shuttle_needs_a_price_and_an_admin(bot: BotHarness) -> None:
    await bot.send(text_update("/shuttle", sender=ALICE))
    await bot.send(text_update("/shuttle 76.79", sender=BOB))

    assert bot.telegram.sent_texts() == [
        "Usage: /shuttle &lt;price&gt;, e.g. /shuttle 76.79",
        NOT_ADMIN,
    ]


async def test_first_batch_from_the_next_session(bot: BotHarness) -> None:
    ids = await seed_members(bot, "frank", "dave")
    upcoming = utc_now() + timedelta(days=4)
    await make_session(bot, starts_at=upcoming, payer_telegram_id=None)

    await bot.send(text_update("/shuttle 76.79", sender=ALICE))
    card = Card(bot)
    assert "Who paid?" in card.latest()["text"]
    await card.tap("frank")
    next_label = f"Next session ({fmt_date(sgt_date(upcoming))})"
    assert set(card.buttons()) == {next_label, "✖ Cancel"}
    await card.tap(next_label)
    await card.tap("✅ Record")

    async with bot.sessionmaker() as db:
        batch = await db.scalar(select(ShuttleBatch))
        audit = await db.scalar(select(AuditLog))
    assert (batch.payer_member_id, batch.amount_cents) == (ids["frank"], 7679)
    assert batch.starts_at <= utc_now()
    assert audit.action == "shuttle.add"
    assert card.latest()["text"] == f"✅ Recorded shuttle batch #{batch.id}."
    assert bot.telegram.sent_texts(GROUP_ID)[-1] == (
        f"🏸 <b>New shuttle batch #{batch.id}</b>: $76.79, paid by frank. "
        "It's used from the next session."
    )


async def test_new_batch_closes_the_previous_and_shows_the_split(bot: BotHarness) -> None:
    ids = await seed_members(bot, "zy2", "shawn2", "frank")
    async with bot.sessionmaker() as db:
        db.add(
            ShuttleBatch(
                payer_member_id=ids["zy2"],
                amount_cents=8000,
                starts_at=utc_now() - timedelta(days=30),
            )
        )
        await db.commit()
    await played(bot, 300, [ids["zy2"], ids["shawn2"]])
    await played(bot, 100, [ids["zy2"]], minutes=60)

    await bot.send(text_update("/shuttle 76.79", sender=ALICE))
    card = Card(bot)
    await card.tap("frank")
    await card.tap("From the next session on")
    await card.tap("✅ Record")

    announcement = bot.telegram.sent_texts(GROUP_ID)[-1].splitlines()
    async with bot.sessionmaker() as db:
        old, new = (await db.scalars(select(ShuttleBatch).order_by(ShuttleBatch.starts_at))).all()
    assert announcement[2:] == [
        f"Batch #{old.id} is finished: $80.00 over 5h = $16.00/h",
        "• zy2 3h — $48.00",
        "• shawn2 2h — $32.00",
        "These amounts go on the next monthly invoice.",
    ]
    assert new.id > old.id


async def test_split_counts_a_session_in_progress(bot: BotHarness) -> None:
    ids = await seed_members(bot, "zy2", "frank", "dave")
    async with bot.sessionmaker() as db:
        db.add(
            ShuttleBatch(
                payer_member_id=ids["zy2"],
                amount_cents=8000,
                starts_at=utc_now() - timedelta(days=30),
            )
        )
        await db.commit()
    await played(bot, 1, [ids["frank"], ids["dave"]])  # 2h, started an hour ago

    await bot.send(text_update("/shuttle 76.79", sender=ALICE))
    card = Card(bot)
    await card.tap("frank")
    await card.tap("From the next session on")
    await card.tap("✅ Record")

    old = (await db_batches(bot))[0]
    assert bot.telegram.sent_texts(GROUP_ID)[-1].splitlines()[2:] == [
        f"Batch #{old.id} is finished: $80.00 over 4h = $20.00/h",
        "• frank 2h — $40.00",
        "• dave 2h — $40.00",
        "These amounts go on the next monthly invoice.",
    ]


async def db_batches(bot: BotHarness) -> list[ShuttleBatch]:
    async with bot.sessionmaker() as db:
        return list(await db.scalars(select(ShuttleBatch).order_by(ShuttleBatch.starts_at)))


async def test_last_session_option_covers_it(bot: BotHarness) -> None:
    ids = await seed_members(bot, "frank")
    last = await played(bot, 5, [ids["frank"]])

    await bot.send(text_update("/shuttle 30", sender=ALICE))
    card = Card(bot)
    await card.tap("frank")
    last_label = next(label for label in card.buttons() if label.startswith("Last session"))
    await card.tap(last_label)
    assert "(the last session)" in card.latest()["text"]
    await card.tap("✅ Record")

    async with bot.sessionmaker() as db:
        batch = await db.scalar(select(ShuttleBatch))
        session = await db.get(Session, last)
    assert batch.starts_at == session.starts_at


async def test_unused_batch_stays_with_its_buyer(bot: BotHarness) -> None:
    ids = await seed_members(bot, "zy2", "frank")
    async with bot.sessionmaker() as db:
        db.add(
            ShuttleBatch(
                payer_member_id=ids["zy2"],
                amount_cents=5000,
                starts_at=utc_now() - timedelta(days=3),
            )
        )
        await db.commit()

    await bot.send(text_update("/shuttle 30", sender=ALICE))
    card = Card(bot)
    await card.tap("frank")
    await card.tap("From the next session on")
    await card.tap("✅ Record")

    assert bot.telegram.sent_texts(GROUP_ID)[-1].endswith(
        "Nobody played with it, so it stays with zy2."
    )


async def test_cancel(bot: BotHarness) -> None:
    await seed_members(bot, "frank")
    await bot.send(text_update("/shuttle 30", sender=ALICE))
    card = Card(bot)

    await card.tap("✖ Cancel")

    assert card.latest()["text"] == "✖ Shuttle purchase not recorded."
    async with bot.sessionmaker() as db:
        assert await db.scalar(select(ShuttleBatch)) is None
