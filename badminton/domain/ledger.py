"""The ledger: every member's credits and debits, derived from stored facts (FR-LED-7).

Sign convention: a positive amount is a credit (the group owes the member), a
negative amount a debit (the member owes the group). Balances always sum to zero.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from collections.abc import Iterable, Sequence
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
class BatchFact:
    """A shuttlecock purchase. It covers sessions starting at or after ``starts_at``
    until the next batch's start (FR-SH-4), and closes when a later-starting batch
    is recorded (FR-SH-2)."""

    id: int
    payer_id: int
    amount_cents: int
    starts_at: datetime
    created_at: datetime


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
    SHUTTLE_PAID = "shuttle_paid"
    SHUTTLE_SHARE = "shuttle_share"
    TRANSFER_SENT = "transfer_sent"
    TRANSFER_RECEIVED = "transfer_received"


@dataclass(frozen=True, slots=True)
class Entry:
    member_id: int
    at: datetime
    kind: EntryKind
    amount_cents: int  # signed: credit > 0, debit < 0
    session_id: int | None = None
    batch_id: int | None = None
    transfer_id: int | None = None
    counterparty_id: int | None = None  # the other member, on transfer entries
    minutes: int | None = None  # time played, on share entries


@dataclass(frozen=True)
class Ledger:
    entries: tuple[Entry, ...]
    balances: dict[int, int]
    # Ended sessions nobody was marked in for: left out of every figure (FR-ATT-7).
    unattended_session_ids: tuple[int, ...] = field(default=())
    # Minutes each member played per batch, for closed and open batches alike.
    batch_minutes: dict[int, dict[int, int]] = field(default_factory=dict)
    # The batch still in use: not billed yet, its minutes so far are "pending" (FR-LED-4).
    open_batch_id: int | None = None

    def pending_minutes(self, member_id: int) -> int:
        if self.open_batch_id is None:
            return 0
        return self.batch_minutes.get(self.open_batch_id, {}).get(member_id, 0)

    def balance(self, member_id: int) -> int:
        return self.balances.get(member_id, 0)

    def entries_for(self, member_id: int) -> list[Entry]:
        return [entry for entry in self.entries if entry.member_id == member_id]


def compute_ledger(
    *,
    sessions: Iterable[SessionFact],
    transfers: Iterable[TransferFact],
    as_of: datetime,
    batches: Iterable[BatchFact] = (),
) -> Ledger:
    """All entries up to ``as_of``, and the resulting balance per member.

    Only batches recorded by ``as_of`` exist; a batch counts as closed once any
    later-starting batch has been recorded.
    """
    known_batches = sorted(
        (batch for batch in batches if batch.created_at <= as_of),
        key=lambda batch: batch.starts_at,
    )
    entries: list[Entry] = []
    unattended: list[int] = []
    batch_minutes: dict[int, dict[int, int]] = {batch.id: {} for batch in known_batches}

    for session in sorted(sessions, key=lambda s: (s.ends_at, s.id)):
        if session.cancelled or session.ends_at > as_of or not session.bookings:
            continue
        if not session.attendee_ids:
            unattended.append(session.id)
            continue
        entries.extend(_court_entries(session))
        if (batch := batch_of(session.starts_at, known_batches)) is not None:
            played = batch_minutes[batch.id]
            for member_id in session.attendee_ids:
                played[member_id] = played.get(member_id, 0) + session.minutes

    for index, batch in enumerate(known_batches):
        later = known_batches[index + 1 :]
        if later:
            closed_at = min(successor.created_at for successor in later)
            entries.extend(_shuttle_entries(batch, batch_minutes[batch.id], closed_at))

    for transfer in sorted(transfers, key=lambda t: (t.at, t.id)):
        if transfer.at <= as_of:
            entries.extend(_transfer_entries(transfer))

    entries.sort(key=lambda entry: entry.at)
    return Ledger(
        entries=tuple(entries),
        balances=_balances(entries),
        unattended_session_ids=tuple(unattended),
        batch_minutes=batch_minutes,
        open_batch_id=known_batches[-1].id if known_batches else None,
    )


def batch_of(starts_at: datetime, batches: Sequence[BatchFact]) -> BatchFact | None:
    """The batch in use for a session starting at ``starts_at``: the one with the latest
    start at or before it. ``batches`` must be sorted by ``starts_at``."""
    index = bisect.bisect_right([batch.starts_at for batch in batches], starts_at)
    return batches[index - 1] if index else None


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


def _shuttle_entries(batch: BatchFact, minutes: dict[int, int], closed_at: datetime) -> list[Entry]:
    """A closed batch: the buyer is credited, players are debited by time played,
    1 hour = 1 share (FR-LED-3). A batch nobody played with stays with its buyer."""
    if not any(minutes.values()):
        return []
    shares = allocate(batch.amount_cents, minutes)
    return [
        Entry(
            batch.payer_id, closed_at, EntryKind.SHUTTLE_PAID, batch.amount_cents, batch_id=batch.id
        ),
        *(
            Entry(
                member_id,
                closed_at,
                EntryKind.SHUTTLE_SHARE,
                -cents,
                batch_id=batch.id,
                minutes=minutes[member_id],
            )
            for member_id, cents in sorted(shares.items())
        ),
    ]


def _transfer_entries(transfer: TransferFact) -> list[Entry]:
    """The sender is credited and the receiver debited (FR-LED-5)."""
    return [
        Entry(
            transfer.from_id,
            transfer.at,
            EntryKind.TRANSFER_SENT,
            transfer.amount_cents,
            transfer_id=transfer.id,
            counterparty_id=transfer.to_id,
        ),
        Entry(
            transfer.to_id,
            transfer.at,
            EntryKind.TRANSFER_RECEIVED,
            -transfer.amount_cents,
            transfer_id=transfer.id,
            counterparty_id=transfer.from_id,
        ),
    ]


def _balances(entries: Iterable[Entry]) -> dict[int, int]:
    balances: defaultdict[int, int] = defaultdict(int)
    for entry in entries:
        balances[entry.member_id] += entry.amount_cents
    return dict(balances)
