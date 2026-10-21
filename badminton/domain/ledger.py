"""The ledger: every member's credits and debits, derived from stored facts (FR-LED-7).

Sign convention: a positive amount is a credit (the group owes the member), a
negative amount a debit (the member owes the group). Balances always sum to zero.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from badminton.domain.money import allocate


@dataclass(frozen=True, slots=True)
class BookingFact:
    """A court payment that has not been cancelled."""

    payer_id: int
    amount_cents: int


@dataclass(frozen=True, slots=True)
class SessionFact:
    id: int
    starts_at: datetime
    ends_at: datetime
    bookings: tuple[BookingFact, ...]
    attendee_ids: frozenset[int]  # members marked "in"; no answer counts as out
    cancelled: bool = False

    @property
    def minutes(self) -> int:
        return int((self.ends_at - self.starts_at).total_seconds() // 60)

    @property
    def court_cents(self) -> int:
        return sum(booking.amount_cents for booking in self.bookings)


@dataclass(frozen=True, slots=True)
class TransferFact:
    id: int
    from_id: int
    to_id: int
    amount_cents: int
    at: datetime


class EntryKind(StrEnum):
    COURT_PAID = "court_paid"
    COURT_SHARE = "court_share"
    TRANSFER_SENT = "transfer_sent"
    TRANSFER_RECEIVED = "transfer_received"


@dataclass(frozen=True, slots=True)
class Entry:
    member_id: int
    at: datetime
    kind: EntryKind
    amount_cents: int  # signed: credit > 0, debit < 0
    session_id: int | None = None
    transfer_id: int | None = None
    minutes: int | None = None  # time played, on share entries


@dataclass(frozen=True)
class Ledger:
    entries: tuple[Entry, ...]
    balances: dict[int, int]
    # Ended sessions nobody was marked in for: left out of every figure (FR-ATT-7).
    unattended_session_ids: tuple[int, ...] = field(default=())

    def balance(self, member_id: int) -> int:
        return self.balances.get(member_id, 0)

    def entries_for(self, member_id: int) -> list[Entry]:
        return [entry for entry in self.entries if entry.member_id == member_id]


def compute_ledger(
    *,
    sessions: Iterable[SessionFact],
    transfers: Iterable[TransferFact],
    as_of: datetime,
) -> Ledger:
    """All entries up to ``as_of``, and the resulting balance per member."""
    entries: list[Entry] = []
    unattended: list[int] = []

    for session in sorted(sessions, key=lambda s: (s.ends_at, s.id)):
        if session.cancelled or session.ends_at > as_of or not session.bookings:
            continue
        if not session.attendee_ids:
            unattended.append(session.id)
            continue
        entries.extend(_court_entries(session))

    for transfer in sorted(transfers, key=lambda t: (t.at, t.id)):
        if transfer.at <= as_of:
            entries.extend(_transfer_entries(transfer))

    entries.sort(key=lambda entry: entry.at)
    return Ledger(tuple(entries), _balances(entries), tuple(unattended))


def _court_entries(session: SessionFact) -> list[Entry]:
    """Bookers are credited what they paid; attendees split the total equally (FR-LED-2)."""
    entries = [
        Entry(
            booking.payer_id,
            session.ends_at,
            EntryKind.COURT_PAID,
            booking.amount_cents,
            session_id=session.id,
        )
        for booking in session.bookings
    ]
    shares = allocate(session.court_cents, dict.fromkeys(session.attendee_ids, 1))
    entries.extend(
        Entry(
            member_id,
            session.ends_at,
            EntryKind.COURT_SHARE,
            -cents,
            session_id=session.id,
            minutes=session.minutes,
        )
        for member_id, cents in sorted(shares.items())
    )
    return entries


def _transfer_entries(transfer: TransferFact) -> list[Entry]:
    """The sender is credited and the receiver debited (FR-LED-5)."""
    return [
        Entry(
            transfer.from_id,
            transfer.at,
            EntryKind.TRANSFER_SENT,
            transfer.amount_cents,
            transfer_id=transfer.id,
        ),
        Entry(
            transfer.to_id,
            transfer.at,
            EntryKind.TRANSFER_RECEIVED,
            -transfer.amount_cents,
            transfer_id=transfer.id,
        ),
    ]


def _balances(entries: Iterable[Entry]) -> dict[int, int]:
    balances: defaultdict[int, int] = defaultdict(int)
    for entry in entries:
        balances[entry.member_id] += entry.amount_cents
    return dict(balances)
