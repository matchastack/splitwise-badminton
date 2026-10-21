"""Monthly statements and group totals (FR-INV-3, FR-INV-5, FR-INV-7).

A statement covers the window since the previous invoice was generated, so the
closing balance is always the opening balance plus the window's entries. If a
correction changed anything dated before the window, the recomputed opening
differs from the closing balance that invoice reported: that difference is
shown as an adjustment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from badminton.domain.ledger import Entry, EntryKind, Ledger


@dataclass(frozen=True)
class Statement:
    member_id: int
    opening_cents: int  # balance at the start of the window, as recomputed now
    adjustment_cents: int  # how far that differs from what the previous invoice reported
    entries: tuple[Entry, ...]  # this member's entries in the window, oldest first
    closing_cents: int

    @property
    def reported_opening_cents(self) -> int:
        """The closing balance the previous invoice showed."""
        return self.opening_cents - self.adjustment_cents

    @property
    def has_activity(self) -> bool:
        return bool(self.entries) or self.adjustment_cents != 0

    def of_kind(self, *kinds: EntryKind) -> list[Entry]:
        return [entry for entry in self.entries if entry.kind in kinds]

    def total(self, *kinds: EntryKind) -> int:
        return sum(entry.amount_cents for entry in self.of_kind(*kinds))


@dataclass(frozen=True)
class BatchClosed:
    batch_id: int
    payer_id: int
    amount_cents: int
    minutes: int  # total time played with the batch


@dataclass(frozen=True)
class GroupTotals:
    session_ids: tuple[int, ...]
    court_cents: int
    batches_closed: tuple[BatchClosed, ...]


def build_statement(
    member_id: int,
    ledger: Ledger,
    *,
    since: datetime | None,
    previous_closing_cents: int | None,
) -> Statement:
    """``member_id``'s statement for entries after ``since`` (the previous invoice's
    generation time, or ``None`` for the first invoice)."""
    mine = ledger.entries_for(member_id)
    before = [entry for entry in mine if since is not None and entry.at <= since]
    window = tuple(entry for entry in mine if since is None or entry.at > since)
    opening = sum(entry.amount_cents for entry in before)
    adjustment = 0 if previous_closing_cents is None else opening - previous_closing_cents
    statement = Statement(member_id, opening, adjustment, window, ledger.balance(member_id))
    assert opening + sum(e.amount_cents for e in window) == statement.closing_cents
    return statement


def group_totals(ledger: Ledger, *, since: datetime | None) -> GroupTotals:
    window = [entry for entry in ledger.entries if since is None or entry.at > since]
    session_ids = sorted({e.session_id for e in window if e.kind is EntryKind.COURT_SHARE})
    court_cents = sum(e.amount_cents for e in window if e.kind is EntryKind.COURT_PAID)
    batches = tuple(
        BatchClosed(
            batch_id=paid.batch_id,
            payer_id=paid.member_id,
            amount_cents=paid.amount_cents,
            minutes=sum(ledger.batch_minutes.get(paid.batch_id, {}).values()),
        )
        for paid in window
        if paid.kind is EntryKind.SHUTTLE_PAID and paid.batch_id is not None
    )
    return GroupTotals(tuple(s for s in session_ids if s is not None), court_cents, batches)
