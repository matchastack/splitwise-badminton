"""Balances from ``Badminton.xlsx``, the spreadsheet the bot replaces (FR-MIG-1, FR-MIG-2).

The ``Items`` sheet has one row per expense: date, item, price, payer, then one share
column per player. Court and shuttle rows split the price in proportion to the
shares. On a transfer row the payer is the sender and the one share holder the
receiver. The ``Total`` sheet's Paid, Spend and Surplus are reproduced exactly, with
fractions, then each balance is rounded to cents so that they still sum to zero.

The latest shuttle row may have no shares yet: a tube nobody has played with. It
can't be split, so it is left out of Spend and becomes the bot's open batch instead,
billed by hours played once the next one is bought (FR-MIG-3).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from fractions import Fraction

COURT = "court"
SHUTTLE = "shuttle"
TRANSFER = "transfer"
ITEMS = (COURT, SHUTTLE, TRANSFER)

DATE = "date"
ITEM = "item"
PRICE = "price"
PAYER = "paid by"
SHARES = "number of shares"  # the player columns sit between PAYER and this one
HEADINGS = (DATE, ITEM, PRICE, PAYER, SHARES)

type Fail = Callable[[str], SheetError]


class SheetError(ValueError):
    """The sheet isn't in the expected shape. The message names the row."""


@dataclass(frozen=True)
class SheetRow:
    number: int  # the spreadsheet's row number, for messages
    day: date | None
    item: str
    price: Fraction
    payer: str
    shares: dict[str, Fraction]  # only players with a share

    @property
    def total_shares(self) -> Fraction:
        return sum(self.shares.values(), Fraction(0))


@dataclass(frozen=True)
class PendingShuttle:
    """The latest shuttle purchase, which nobody has played with yet."""

    row: int
    day: date | None
    payer: str
    amount_cents: int


@dataclass(frozen=True)
class SheetSummary:
    players: tuple[str, ...]  # column order; players with no activity are left out
    paid: dict[str, Fraction]
    spend: dict[str, Fraction]
    pending: PendingShuttle | None
    last_day: date
    row_count: int

    def surplus(self, name: str) -> Fraction:
        return self.paid[name] - self.spend[name]

    def pending_cents(self, name: str) -> int:
        """The unused tube, for the player who bought it."""
        if self.pending is None or self.pending.payer != name:
            return 0
        return self.pending.amount_cents

    def opening(self, name: str) -> Fraction:
        """The exact balance the bot carries over: the surplus, less the unused tube.
        The bot credits a tube to its buyer only once it's finished (FR-LED-4)."""
        return self.surplus(name) - Fraction(self.pending_cents(name), 100)

    def opening_cents(self) -> dict[str, int]:
        return zero_sum_cents({name: self.opening(name) for name in self.players})


def read_items(
    header: Sequence[object], rows: Iterable[tuple[int, Sequence[object]]]
) -> SheetSummary:
    """Summarise the ``Items`` sheet: ``header`` is its first row and ``rows`` the
    others, as (row number, cell values). Blank rows are skipped."""
    columns = _columns(header)
    players = [_text(header[i]) for i in range(columns[PAYER] + 1, columns[SHARES])]
    if not players or not all(players) or len(set(players)) != len(players):
        raise SheetError(
            "Row 1: each column between 'Paid By' and 'Number of Shares' needs a name."
        )
    parsed = []
    for number, values in rows:
        cells = [*values, *[None] * (len(header) - len(values))]
        if any(_text(cell) for cell in cells[: columns[SHARES]]):
            parsed.append(_parse_row(number, cells, columns, players))
    return summarise(players, parsed)


def summarise(players: Sequence[str], rows: Sequence[SheetRow]) -> SheetSummary:
    """Paid, Spend and the unused tube per player, as the ``Total`` sheet computes them."""
    pending = _pending_shuttle(rows)
    paid = dict.fromkeys(players, Fraction(0))
    spend = dict.fromkeys(players, Fraction(0))
    active: set[str] = set()
    for row in rows:
        paid[row.payer] += row.price
        active.add(row.payer)
        for name, share in row.shares.items():
            spend[name] += row.price * share / row.total_shares
            active.add(name)

    days = [row.day for row in rows if row.day is not None]
    if not days:
        raise SheetError("No row has a date, so there's no date the balances run up to.")
    kept = tuple(name for name in players if name in active)
    return SheetSummary(
        players=kept,
        paid={name: paid[name] for name in kept},
        spend={name: spend[name] for name in kept},
        pending=pending,
        last_day=max(days),
        row_count=len(rows),
    )


def zero_sum_cents(amounts: dict[str, Fraction]) -> dict[str, int]:
    """Round dollar amounts that sum to zero into cents that still sum to zero.

    Largest remainder, as in ``money.allocate``: everyone gets the floor of their
    exact amount, then the leftover cents go one each to the largest remainders.
    Ties go to the earlier key.
    """
    if sum(amounts.values(), Fraction(0)) != 0:
        raise ValueError("amounts must sum to zero")
    exact = {name: amount * 100 for name, amount in amounts.items()}
    cents = {name: math.floor(value) for name, value in exact.items()}
    order = list(amounts)
    by_remainder = sorted(order, key=lambda name: (cents[name] - exact[name], order.index(name)))
    for name in by_remainder[: -sum(cents.values())]:
        cents[name] += 1
    return cents


def _columns(header: Sequence[object]) -> dict[str, int]:
    names = [_text(cell).casefold() for cell in header]
    missing = [heading for heading in HEADINGS if heading not in names]
    if missing:
        raise SheetError(f"Row 1: missing column(s) {', '.join(map(repr, missing))}.")
    return {heading: names.index(heading) for heading in HEADINGS}


def _parse_row(
    number: int, cells: Sequence[object], columns: dict[str, int], players: Sequence[str]
) -> SheetRow:
    def fail(message: str) -> SheetError:
        return SheetError(f"Row {number}: {message}")

    item = _text(cells[columns[ITEM]]).casefold()
    if item not in ITEMS:
        raise fail(f"the item must be {', '.join(ITEMS[:-1])} or {ITEMS[-1]}, not {item!r}.")

    price = _number(cells[columns[PRICE]], fail, "the price")
    if price <= 0 or (price * 100).denominator != 1:
        raise fail(f"the price must be a positive amount in whole cents, not {float(price)}.")

    payer = _text(cells[columns[PAYER]])
    if payer not in players:
        raise fail(f"'Paid By' must be one of the player columns, not {payer!r}.")

    shares = {}
    for column, name in enumerate(players, start=columns[PAYER] + 1):
        share = _number(cells[column], fail, f"{name}'s share", blank=Fraction(0))
        if share < 0:
            raise fail(f"{name}'s share can't be negative.")
        if share:
            shares[name] = share

    if item == TRANSFER and (len(shares) != 1 or payer in shares):
        raise fail("a transfer needs exactly one receiver, other than the sender in 'Paid By'.")
    if item == COURT and not shares:
        raise fail("a court row needs at least one share.")
    return SheetRow(number, _day(cells[columns[DATE]], fail), item, price, payer, shares)


def _pending_shuttle(rows: Sequence[SheetRow]) -> PendingShuttle | None:
    """The unused tube: a shuttle row with no shares, which must be the latest one."""
    unshared = [row for row in rows if not row.shares]
    if not unshared:
        return None
    latest = [row for row in rows if row.item == SHUTTLE][-1]
    for row in unshared:
        if row is not latest:
            raise SheetError(
                f"Row {row.number}: only the latest shuttle purchase may have no shares yet."
            )
    return PendingShuttle(latest.number, latest.day, latest.payer, int(latest.price * 100))


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _number(value: object, fail: Fail, what: str, *, blank: Fraction | None = None) -> Fraction:
    if not _text(value):
        if blank is None:
            raise fail(f"{what} is empty.")
        return blank
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise fail(f"{what} must be a number, not {value!r}.")
    try:
        # str() gives a float's shortest decimal, so 14.8 is exactly 74/5.
        return Fraction(_text(value))
    except ValueError:
        raise fail(f"{what} must be a number, not {value!r}.") from None


def _day(value: object, fail: Fail) -> date | None:
    if not _text(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise fail(f"the date must be a date, not {value!r}.")
