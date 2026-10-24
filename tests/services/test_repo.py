from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from badminton.db.models import Attendance, Booking, Member, Session, ShuttleBatch, Transfer
from badminton.domain.ledger import (
    BatchFact,
    BookingFact,
    SessionFact,
    TransferFact,
    compute_ledger,
)
from badminton.domain.timeutil import at_sgt
from badminton.services.repo import get_balances, last_hourly_rate, load_facts, load_ledger

NOW = at_sgt(date(2026, 11, 1), 10)


async def seed(db: AsyncSession) -> dict[str, int]:
    names = ["alice", "bob", "carol", "dave"]
    members = [Member(display_name=name, telegram_user_id=100 + i) for i, name in enumerate(names)]
    db.add_all(members)
    await db.flush()
    ids = {member.display_name: member.id for member in members}

    def add_session(day: date, status: str = "scheduled") -> Session:
        start = at_sgt(day, 11)
        session = Session(starts_at=start, ends_at=start + timedelta(hours=2), status=status)
        db.add(session)
        return session

    first = add_session(date(2026, 10, 4))
    second = add_session(date(2026, 10, 11))
    cancelled = add_session(date(2026, 10, 18), status="cancelled")
    await db.flush()

    for session, payer, cents, cancelled_at in [
        (first, "alice", 1480, None),
        (second, "dave", 740, None),
        (second, "alice", 740, None),
        (second, "carol", 999, datetime(2026, 10, 1, tzinfo=UTC)),  # a cancelled booking
        (cancelled, "alice", 1480, None),
    ]:
        db.add(
            Booking(
                session_id=session.id,
                payer_member_id=ids[payer],
                submitted_by_member_id=ids[payer],
                starts_at=session.starts_at,
                minutes=120,
                amount_cents=cents,
                cancelled_at=cancelled_at,
            )
        )
    for session, ins, outs in [
        (first, ["alice", "bob", "carol"], ["dave"]),
        (second, ["bob", "dave"], ["alice"]),
        (cancelled, ["alice"], []),
    ]:
        db.add_all(Attendance(session_id=session.id, member_id=ids[n], status="in") for n in ins)
        db.add_all(Attendance(session_id=session.id, member_id=ids[n], status="out") for n in outs)
    db.add_all(
        [
            ShuttleBatch(
                payer_member_id=ids["alice"],
                amount_cents=6000,
                starts_at=at_sgt(date(2026, 10, 1), 0),
            ),
            ShuttleBatch(
                payer_member_id=ids["dave"],
                amount_cents=7000,
                starts_at=at_sgt(date(2026, 10, 12), 0),
            ),
            Transfer(from_member_id=ids["bob"], to_member_id=ids["alice"], amount_cents=500),
        ]
    )
    await db.commit()
    return ids | {"first": first.id, "second": second.id, "cancelled": cancelled.id}


async def test_loads_facts_matching_the_database(db: AsyncSession) -> None:
    ids = await seed(db)

    facts = await load_facts(db)

    first, second, cancelled = facts.sessions
    assert first.id == ids["first"]
    assert first.bookings == (BookingFact(ids["alice"], 1480),)
    assert first.attendee_ids == {ids["alice"], ids["bob"], ids["carol"]}
    assert second.bookings == (BookingFact(ids["dave"], 740), BookingFact(ids["alice"], 740))
    assert second.attendee_ids == {ids["bob"], ids["dave"]}
    assert cancelled.cancelled
    assert [(b.payer_id, b.amount_cents) for b in facts.batches] == [
        (ids["alice"], 6000),
        (ids["dave"], 7000),
    ]
    assert [(t.from_id, t.to_id, t.amount_cents) for t in facts.transfers] == [
        (ids["bob"], ids["alice"], 500)
    ]


async def test_ledger_from_database_matches_pure_ledger(db: AsyncSession) -> None:
    ids = await seed(db)
    facts = await load_facts(db)
    alice, bob, carol, dave = (ids[name] for name in ("alice", "bob", "carol", "dave"))
    expected = compute_ledger(
        sessions=[
            SessionFact(
                ids["first"],
                at_sgt(date(2026, 10, 4), 11),
                at_sgt(date(2026, 10, 4), 13),
                (BookingFact(alice, 1480),),
                frozenset({alice, bob, carol}),
            ),
            SessionFact(
                ids["second"],
                at_sgt(date(2026, 10, 11), 11),
                at_sgt(date(2026, 10, 11), 13),
                (BookingFact(dave, 740), BookingFact(alice, 740)),
                frozenset({bob, dave}),
            ),
        ],
        transfers=[TransferFact(1, bob, alice, 500, facts.transfers[0].at)],
        batches=[
            BatchFact(b.id, b.payer_id, b.amount_cents, b.starts_at, b.created_at)
            for b in facts.batches
        ],
        as_of=NOW,
    )

    ledger = await load_ledger(db, NOW)

    assert ledger.balances == expected.balances
    assert await get_balances(db, NOW) == expected.balances
    assert sum(ledger.balances.values()) == 0
    # Batches were created "now" in the test, so the first one is already closed.
    assert ledger.open_batch_id == facts.batches[-1].id


async def test_last_hourly_rate(db: AsyncSession) -> None:
    assert await last_hourly_rate(db, 740) == 740

    ids = await seed(db)
    db.add(
        Booking(
            session_id=ids["first"],
            payer_member_id=ids["alice"],
            submitted_by_member_id=ids["alice"],
            starts_at=at_sgt(date(2026, 10, 4), 11),
            minutes=90,
            amount_cents=1200,
        )
    )
    await db.commit()

    assert await last_hourly_rate(db, 740) == 800
