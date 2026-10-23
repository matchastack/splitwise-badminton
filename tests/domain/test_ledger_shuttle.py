from datetime import date, datetime, timedelta

from badminton.domain.ledger import (
    BatchFact,
    BookingFact,
    EntryKind,
    SessionFact,
    batch_of,
    compute_ledger,
)
from badminton.domain.timeutil import at_sgt

ALICE, BOB, CAROL, DAVE, ERIN, FRANK = 1, 2, 3, 4, 5, 6


def sunday(
    day: date, *, attendees: tuple[int, ...], session_id: int, hours: int = 2
) -> SessionFact:
    start = at_sgt(day, 11)
    return SessionFact(
        id=session_id,
        starts_at=start,
        ends_at=start + timedelta(hours=hours),
        bookings=(BookingFact(ALICE, 740 * hours),),
        attendee_ids=frozenset(attendees),
    )


def batch(
    batch_id: int, payer: int, cents: int, starts_at: datetime, created_at: datetime | None = None
) -> BatchFact:
    return BatchFact(batch_id, payer, cents, starts_at, created_at or starts_at)


def shuttle_shares(ledger, batch_id: int) -> dict[int, tuple[int, int]]:
    return {
        e.member_id: (-e.amount_cents, e.minutes)
        for e in ledger.entries
        if e.kind is EntryKind.SHUTTLE_SHARE and e.batch_id == batch_id
    }


# Attendance from Badminton.xlsx between the 16 Aug and 18 Oct 2026 purchases.
SHEET_SESSIONS = [
    sunday(date(2026, 8, 16), attendees=(ALICE, BOB, CAROL, DAVE, ERIN), session_id=1),
    sunday(date(2026, 8, 30), attendees=(ALICE, BOB, DAVE, ERIN), session_id=2),
    sunday(date(2026, 9, 6), attendees=(ALICE, DAVE, ERIN), session_id=3),
    sunday(date(2026, 9, 13), attendees=(ALICE, BOB, DAVE, ERIN), session_id=4),
    sunday(date(2026, 10, 4), attendees=(ALICE, BOB, DAVE, ERIN), session_id=5),
    sunday(date(2026, 10, 11), attendees=(ALICE, BOB, CAROL, DAVE, ERIN), session_id=6),
    sunday(date(2026, 10, 18), attendees=(ALICE, BOB, DAVE, ERIN, FRANK), session_id=7),
]
EARLIER = batch(1, ERIN, 3874, at_sgt(date(2026, 7, 20), 12))
AUG_16 = batch(2, ALICE, 8000, at_sgt(date(2026, 8, 16), 13))  # bought after that day's session
OCT_18 = batch(3, FRANK, 7679, at_sgt(date(2026, 10, 18), 13))


def test_reproduces_the_sheets_16_aug_batch() -> None:
    ledger = compute_ledger(
        sessions=SHEET_SESSIONS,
        transfers=[],
        batches=[EARLIER, AUG_16, OCT_18],
        as_of=at_sgt(date(2026, 10, 19), 0),
    )

    # $80 over 50 hours = $1.60/h, exactly as the sheet's SUMPRODUCT gives.
    assert shuttle_shares(ledger, AUG_16.id) == {
        ALICE: (1920, 720),
        BOB: (1600, 600),
        CAROL: (320, 120),
        DAVE: (1920, 720),
        ERIN: (1920, 720),
        FRANK: (320, 120),
    }
    paid = [
        e for e in ledger.entries if e.kind is EntryKind.SHUTTLE_PAID and e.batch_id == AUG_16.id
    ]
    assert [(e.member_id, e.amount_cents, e.at) for e in paid] == [(ALICE, 8000, OCT_18.created_at)]
    # The 16 Aug session itself was the earlier batch's: $38.74 over 5 people, 2h each.
    assert shuttle_shares(ledger, EARLIER.id) == {
        ALICE: (775, 120),
        BOB: (775, 120),
        CAROL: (775, 120),
        DAVE: (775, 120),
        ERIN: (774, 120),
    }
    assert sum(ledger.balances.values()) == 0


def test_open_batch_is_not_billed_but_reports_pending_hours() -> None:
    sessions = [*SHEET_SESSIONS, sunday(date(2026, 10, 25), attendees=(ALICE, CAROL), session_id=8)]

    ledger = compute_ledger(
        sessions=sessions,
        transfers=[],
        batches=[AUG_16, OCT_18],
        as_of=at_sgt(date(2026, 10, 26), 0),
    )

    assert ledger.open_batch_id == OCT_18.id
    assert not [e for e in ledger.entries if e.batch_id == OCT_18.id]
    assert ledger.pending_minutes(ALICE) == 120
    assert ledger.pending_minutes(CAROL) == 120
    assert ledger.pending_minutes(BOB) == 0


def test_session_starting_exactly_at_batch_start_uses_that_batch() -> None:
    """The "Last session" start point: the batch starts at that session's start."""
    last = SHEET_SESSIONS[-1]
    bought_after = batch(3, FRANK, 7679, last.starts_at, created_at=last.ends_at + timedelta(hours=1))

    ledger = compute_ledger(
        sessions=SHEET_SESSIONS,
        transfers=[],
        batches=[AUG_16, bought_after],
        as_of=at_sgt(date(2026, 10, 19), 0),
    )

    assert ledger.batch_minutes[bought_after.id] == {ALICE: 120, BOB: 120, DAVE: 120, ERIN: 120, FRANK: 120}
    assert FRANK not in ledger.batch_minutes[AUG_16.id]


def test_batch_closes_only_once_its_successor_is_recorded() -> None:
    successor_recorded = OCT_18.created_at

    before = compute_ledger(
        sessions=SHEET_SESSIONS,
        transfers=[],
        batches=[AUG_16, OCT_18],
        as_of=successor_recorded - timedelta(minutes=1),
    )
    after = compute_ledger(
        sessions=SHEET_SESSIONS, transfers=[], batches=[AUG_16, OCT_18], as_of=successor_recorded
    )

    assert before.open_batch_id == AUG_16.id
    assert not [e for e in before.entries if e.batch_id == AUG_16.id]
    assert after.open_batch_id == OCT_18.id
    assert shuttle_shares(after, AUG_16.id)


def test_backfilled_batch_closes_earlier_one_at_first_later_purchase() -> None:
    first = batch(1, ALICE, 6000, at_sgt(date(2026, 8, 1), 0))
    third = batch(3, DAVE, 6000, at_sgt(date(2026, 10, 1), 0))
    backfilled = batch(
        2, ERIN, 6000, at_sgt(date(2026, 9, 1), 0), created_at=at_sgt(date(2026, 10, 20), 0)
    )

    ledger = compute_ledger(
        sessions=SHEET_SESSIONS,
        transfers=[],
        batches=[first, third, backfilled],
        as_of=at_sgt(date(2026, 10, 21), 0),
    )

    closing_times = {e.batch_id: e.at for e in ledger.entries if e.kind is EntryKind.SHUTTLE_PAID}
    assert closing_times == {first.id: third.created_at, backfilled.id: third.created_at}


def test_closed_batch_nobody_played_stays_with_buyer() -> None:
    unused = batch(1, ALICE, 5000, at_sgt(date(2026, 1, 1), 0))
    replacement = batch(2, DAVE, 5000, at_sgt(date(2026, 2, 1), 0))

    ledger = compute_ledger(
        sessions=[], transfers=[], batches=[unused, replacement], as_of=at_sgt(date(2026, 3, 1), 0)
    )

    assert ledger.entries == ()


def test_sessions_before_the_first_batch_have_no_shuttle_cost() -> None:
    ledger = compute_ledger(
        sessions=SHEET_SESSIONS[:2],
        transfers=[],
        batches=[OCT_18],
        as_of=at_sgt(date(2026, 10, 19), 0),
    )

    assert ledger.batch_minutes == {OCT_18.id: {}}
    assert not [e for e in ledger.entries if e.batch_id is not None]


def test_unattended_sessions_add_no_hours() -> None:
    empty = sunday(date(2026, 10, 25), attendees=(), session_id=8)

    ledger = compute_ledger(
        sessions=[empty], transfers=[], batches=[OCT_18], as_of=at_sgt(date(2026, 10, 26), 0)
    )

    assert ledger.batch_minutes[OCT_18.id] == {}
    assert ledger.unattended_session_ids == (8,)


def test_batch_of() -> None:
    batches = [EARLIER, AUG_16, OCT_18]

    assert batch_of(at_sgt(date(2026, 7, 1), 11), batches) is None
    assert batch_of(AUG_16.starts_at, batches) is AUG_16
    assert batch_of(at_sgt(date(2026, 8, 16), 11), batches) is EARLIER
    assert batch_of(at_sgt(date(2027, 1, 1), 11), batches) is OCT_18
