from datetime import date, timedelta

from badminton.domain.ledger import (
    BookingFact,
    EntryKind,
    OpeningFact,
    SessionFact,
    compute_ledger,
)
from badminton.domain.timeutil import at_sgt

ALICE, BOB, FRANK = 1, 2, 3
IMPORTED = at_sgt(date(2026, 10, 23), 9)


def test_opening_balances_count_from_when_they_were_imported() -> None:
    openings = [OpeningFact(ALICE, 1452, IMPORTED), OpeningFact(FRANK, -1452, IMPORTED)]
    start = at_sgt(date(2026, 10, 25), 11)
    session = SessionFact(
        1, start, start + timedelta(hours=2), (BookingFact(ALICE, 1480),), frozenset({ALICE, BOB})
    )

    before = compute_ledger(
        sessions=[], transfers=[], openings=openings, as_of=IMPORTED - timedelta(seconds=1)
    )
    after = compute_ledger(
        sessions=[session], transfers=[], openings=openings, as_of=start + timedelta(hours=3)
    )

    assert before.balances == {}
    assert after.balances == {ALICE: 1452 + 1480 - 740, BOB: -740, FRANK: -1452}
    assert sum(after.balances.values()) == 0
    opening_entries = [e for e in after.entries if e.kind is EntryKind.OPENING_BALANCE]
    assert [(e.member_id, e.amount_cents, e.at) for e in opening_entries] == [
        (ALICE, 1452, IMPORTED),
        (FRANK, -1452, IMPORTED),
    ]
    assert after.entries[0].kind is EntryKind.OPENING_BALANCE  # oldest first
