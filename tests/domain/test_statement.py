from datetime import date, timedelta

from badminton.domain.ledger import (
    BatchFact,
    BookingFact,
    EntryKind,
    SessionFact,
    TransferFact,
    compute_ledger,
)
from badminton.domain.statement import BatchClosed, build_statement, group_totals
from badminton.domain.timeutil import at_sgt

ZY, SHAWN, HW = 1, 2, 3
SEP_INVOICE = at_sgt(date(2026, 10, 1), 10)  # the previous invoice was generated here
NOW = at_sgt(date(2026, 11, 1), 10)


def session(session_id: int, day: date, attendees: tuple[int, ...], payer: int = ZY) -> SessionFact:
    start = at_sgt(day, 11)
    return SessionFact(
        session_id,
        start,
        start + timedelta(hours=2),
        (BookingFact(payer, 1500),),
        frozenset(attendees),
    )


SEPTEMBER = session(1, date(2026, 9, 27), (ZY, SHAWN, HW))
OCTOBER = [
    session(2, date(2026, 10, 4), (ZY, SHAWN, HW)),
    session(3, date(2026, 10, 11), (SHAWN, HW), payer=HW),
]
BATCHES = [
    BatchFact(1, ZY, 6000, at_sgt(date(2026, 9, 1), 0), at_sgt(date(2026, 9, 1), 0)),
    BatchFact(2, HW, 6000, at_sgt(date(2026, 10, 12), 0), at_sgt(date(2026, 10, 12), 0)),
]
TRANSFERS = [TransferFact(1, SHAWN, ZY, 500, at_sgt(date(2026, 10, 2), 9))]


def ledger(sessions: list[SessionFact]):
    return compute_ledger(sessions=sessions, transfers=TRANSFERS, batches=BATCHES, as_of=NOW)


def test_statement_lists_the_windows_activity() -> None:
    current = ledger([SEPTEMBER, *OCTOBER])
    september_closing = compute_ledger(
        sessions=[SEPTEMBER], transfers=[], batches=BATCHES, as_of=SEP_INVOICE
    ).balance(SHAWN)

    statement = build_statement(
        SHAWN, current, since=SEP_INVOICE, previous_closing_cents=september_closing
    )

    assert statement.opening_cents == -500  # his September court share
    assert statement.adjustment_cents == 0
    assert [(e.kind, e.amount_cents) for e in statement.entries] == [
        (EntryKind.TRANSFER_SENT, 500),
        (EntryKind.COURT_SHARE, -500),
        (EntryKind.COURT_SHARE, -750),
        (EntryKind.SHUTTLE_SHARE, -2250),  # batch 1 closed on 12 Oct: $60 over 16h, his 6h
    ]
    assert statement.of_kind(EntryKind.TRANSFER_SENT)[0].counterparty_id == ZY
    assert statement.total(EntryKind.COURT_SHARE) == -1250
    assert statement.closing_cents == current.balance(SHAWN)
    assert statement.closing_cents == statement.opening_cents + sum(
        e.amount_cents for e in statement.entries
    )
    assert statement.has_activity


def test_member_without_activity() -> None:
    current = ledger([SEPTEMBER, *OCTOBER])

    statement = build_statement(99, current, since=SEP_INVOICE, previous_closing_cents=None)

    assert not statement.has_activity
    assert statement.opening_cents == statement.closing_cents == 0


def test_first_invoice_covers_everything() -> None:
    current = ledger([SEPTEMBER, *OCTOBER])

    statement = build_statement(ZY, current, since=None, previous_closing_cents=None)

    assert statement.opening_cents == 0
    assert statement.closing_cents == current.balance(ZY)
    assert len(statement.entries) == len(current.entries_for(ZY))


def test_correcting_an_invoiced_month_shows_as_adjustment() -> None:
    reported = compute_ledger(
        sessions=[SEPTEMBER], transfers=[], batches=BATCHES, as_of=SEP_INVOICE
    )
    # After that invoice the admin removes HW from the September session.
    corrected_september = session(1, date(2026, 9, 27), (ZY, SHAWN))
    current = ledger([corrected_september, *OCTOBER])

    statement = build_statement(
        SHAWN, current, since=SEP_INVOICE, previous_closing_cents=reported.balance(SHAWN)
    )

    assert statement.reported_opening_cents == -500
    assert statement.opening_cents == -750
    assert statement.adjustment_cents == -250
    assert statement.has_activity


def test_group_totals_for_the_window() -> None:
    totals = group_totals(ledger([SEPTEMBER, *OCTOBER]), since=SEP_INVOICE)

    assert totals.session_ids == (2, 3)
    assert totals.court_cents == 3000
    assert totals.batches_closed == (
        BatchClosed(batch_id=1, payer_id=ZY, amount_cents=6000, minutes=960),
    )
