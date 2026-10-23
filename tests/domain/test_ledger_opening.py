from datetime import date, timedelta

from badminton.domain.ledger import (
    BookingFact,
    EntryKind,
    OpeningFact,
    SessionFact,
    compute_ledger,
)
from badminton.domain.timeutil import at_sgt

ZY, SHAWN, YT = 1, 2, 3
IMPORTED = at_sgt(date(2026, 10, 23), 9)


def test_opening_balances_count_from_when_they_were_imported() -> None:
    openings = [OpeningFact(ZY, 1452, IMPORTED), OpeningFact(YT, -1452, IMPORTED)]
    start = at_sgt(date(2026, 10, 25), 11)
    session = SessionFact(
        1, start, start + timedelta(hours=2), (BookingFact(ZY, 1480),), frozenset({ZY, SHAWN})
    )

    before = compute_ledger(
        sessions=[], transfers=[], openings=openings, as_of=IMPORTED - timedelta(seconds=1)
    )
    after = compute_ledger(
        sessions=[session], transfers=[], openings=openings, as_of=start + timedelta(hours=3)
    )

    assert before.balances == {}
    assert after.balances == {ZY: 1452 + 1480 - 740, SHAWN: -740, YT: -1452}
    assert sum(after.balances.values()) == 0
    opening_entries = [e for e in after.entries if e.kind is EntryKind.OPENING_BALANCE]
    assert [(e.member_id, e.amount_cents, e.at) for e in opening_entries] == [
        (ZY, 1452, IMPORTED),
        (YT, -1452, IMPORTED),
    ]
    assert after.entries[0].kind is EntryKind.OPENING_BALANCE  # oldest first
