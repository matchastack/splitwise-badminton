import random
from datetime import date, datetime, timedelta

import pytest

from badminton.domain.ledger import (
    BookingFact,
    EntryKind,
    SessionFact,
    TransferFact,
    compute_ledger,
)
from badminton.domain.timeutil import at_sgt

ALICE, BOB, CAROL, DAVE, ERIN = 1, 2, 3, 4, 5
START = at_sgt(date(2026, 10, 4), 11)
END = START + timedelta(hours=2)
AFTER = END + timedelta(days=1)


def session(
    *,
    session_id: int = 1,
    start: datetime = START,
    hours: float = 2,
    bookings: tuple[tuple[int, int], ...] = ((ALICE, 1480),),
    attendees: tuple[int, ...] = (ALICE, BOB, DAVE, ERIN),
    cancelled: bool = False,
) -> SessionFact:
    return SessionFact(
        id=session_id,
        starts_at=start,
        ends_at=start + timedelta(hours=hours),
        bookings=tuple(BookingFact(payer, cents) for payer, cents in bookings),
        attendee_ids=frozenset(attendees),
        cancelled=cancelled,
    )


def test_court_split_equally_and_booker_credited() -> None:
    ledger = compute_ledger(sessions=[session()], transfers=[], as_of=AFTER)

    assert ledger.balances == {ALICE: 1480 - 370, BOB: -370, DAVE: -370, ERIN: -370}
    shares = [e for e in ledger.entries if e.kind is EntryKind.COURT_SHARE]
    assert {e.member_id: (e.amount_cents, e.minutes) for e in shares} == {
        ALICE: (-370, 120),
        BOB: (-370, 120),
        DAVE: (-370, 120),
        ERIN: (-370, 120),
    }
    assert all(e.at == END and e.session_id == 1 for e in ledger.entries)


def test_leftover_cents_go_to_lowest_member_ids() -> None:
    ledger = compute_ledger(
        sessions=[session(attendees=(ERIN, BOB, DAVE))], transfers=[], as_of=AFTER
    )

    assert ledger.balance(BOB) == -494
    assert ledger.balance(DAVE) == -493
    assert ledger.balance(ERIN) == -493
    assert ledger.balance(ALICE) == 1480


def test_booker_who_does_not_play_is_still_credited() -> None:
    ledger = compute_ledger(
        sessions=[session(bookings=((CAROL, 1480),), attendees=(ALICE, BOB))],
        transfers=[],
        as_of=AFTER,
    )

    assert ledger.balances == {CAROL: 1480, ALICE: -740, BOB: -740}


def test_multiple_bookings_add_up() -> None:
    ledger = compute_ledger(
        sessions=[
            session(bookings=((DAVE, 740), (ALICE, 740)), attendees=(ALICE, BOB, CAROL, DAVE))
        ],
        transfers=[],
        as_of=AFTER,
    )

    assert ledger.balances == {DAVE: 740 - 370, ALICE: 740 - 370, BOB: -370, CAROL: -370}


def test_only_ended_sessions_count() -> None:
    sessions = [session()]

    assert (
        compute_ledger(sessions=sessions, transfers=[], as_of=END - timedelta(minutes=1)).balances
        == {}
    )
    assert compute_ledger(sessions=sessions, transfers=[], as_of=END).balance(ALICE) == 1110


def test_cancelled_and_bookingless_sessions_are_ignored() -> None:
    ledger = compute_ledger(
        sessions=[session(cancelled=True), session(session_id=2, bookings=())],
        transfers=[],
        as_of=AFTER,
    )

    assert ledger.entries == ()
    assert ledger.unattended_session_ids == ()


def test_session_without_attendees_is_excluded_and_flagged() -> None:
    ledger = compute_ledger(
        sessions=[session(attendees=()), session(session_id=2, start=START + timedelta(days=7))],
        transfers=[],
        as_of=AFTER + timedelta(days=7),
    )

    assert ledger.unattended_session_ids == (1,)
    assert {e.session_id for e in ledger.entries} == {2}


def test_transfers_credit_sender_and_debit_receiver() -> None:
    transfer = TransferFact(id=7, from_id=BOB, to_id=ALICE, amount_cents=370, at=AFTER)

    ledger = compute_ledger(sessions=[session()], transfers=[transfer], as_of=AFTER)

    assert ledger.balance(BOB) == 0
    assert ledger.balance(ALICE) == 1110 - 370
    kinds = {(e.member_id, e.kind) for e in ledger.entries if e.transfer_id == 7}
    assert kinds == {(BOB, EntryKind.TRANSFER_SENT), (ALICE, EntryKind.TRANSFER_RECEIVED)}


def test_transfers_after_as_of_are_ignored() -> None:
    transfer = TransferFact(id=7, from_id=BOB, to_id=ALICE, amount_cents=370, at=AFTER)

    assert compute_ledger(sessions=[], transfers=[transfer], as_of=END).balances == {}


def test_entries_are_in_time_order() -> None:
    later = session(session_id=2, start=START + timedelta(days=7))
    transfer = TransferFact(id=1, from_id=BOB, to_id=ALICE, amount_cents=100, at=AFTER)

    ledger = compute_ledger(
        sessions=[later, session()], transfers=[transfer], as_of=AFTER + timedelta(days=30)
    )

    times = [entry.at for entry in ledger.entries]
    assert times == sorted(times)


def test_balances_always_sum_to_zero() -> None:
    rng = random.Random(7)
    members = list(range(1, 10))
    sessions = [
        session(
            session_id=i,
            start=START + timedelta(days=7 * i),
            hours=rng.choice([1, 2, 3]),
            bookings=tuple(
                (rng.choice(members), rng.randint(500, 3000)) for _ in range(rng.randint(1, 3))
            ),
            attendees=tuple(rng.sample(members, rng.randint(0, 8))),
        )
        for i in range(60)
    ]
    transfers = [
        TransferFact(
            i,
            *rng.sample(members, 2),
            amount_cents=rng.randint(1, 9000),
            at=START + timedelta(days=i),
        )
        for i in range(40)
    ]

    ledger = compute_ledger(
        sessions=sessions, transfers=transfers, as_of=START + timedelta(days=500)
    )

    assert sum(ledger.balances.values()) == 0
    assert ledger.unattended_session_ids  # the scenario includes some empty sessions


@pytest.mark.parametrize("hours", [1, 1.5, 2, 3])
def test_session_minutes(hours: float) -> None:
    assert session(hours=hours).minutes == int(hours * 60)
