from datetime import date, datetime
from fractions import Fraction

import pytest

from badminton.domain.sheet import PendingShuttle, SheetError, read_items, zero_sum_cents

HEADER = (
    "Date",
    "Item",
    "Price",
    "Paid By",
    "alice",
    "bob",
    "carol",
    "frank",
    "others",
    "Number of Shares",
    "Price per Share",
    "Remarks",
)


def day(month: int, dom: int) -> datetime:
    return datetime(2026, month, dom)  # openpyxl reads dates as naive datetimes


def sheet(*rows: tuple[object, ...]):
    return read_items(HEADER, [(number, row) for number, row in enumerate(rows, start=2)])


def test_reproduces_the_total_sheet() -> None:
    summary = sheet(
        (day(10, 4), "court", 14.8, "alice", 1, 1, 1, None, None, "=sum(E2:I2)", "=C2/J2"),
        (day(10, 11), "Court", 7.4, "bob", 0.5, 0.5, None, None),  # 1h, half shares
        (day(10, 12), "shuttle", 80, "carol", 6, 5, 1, None),
        (day(10, 18), "transfer", 20, "bob", 1, None, None, None),  # bob pays alice
    )

    assert summary.players == ("alice", "bob", "carol")  # frank and others never appear
    assert summary.paid == {"alice": Fraction("14.8"), "bob": Fraction("27.4"), "carol": 80}
    assert summary.spend == {
        "alice": Fraction("14.8") / 3 + Fraction("3.7") + 40 + 20,
        "bob": Fraction("14.8") / 3 + Fraction("3.7") + Fraction(80 * 5, 12),
        "carol": Fraction("14.8") / 3 + Fraction(80, 12),
    }
    assert sum(map(summary.surplus, summary.players)) == 0
    assert summary.pending is None
    assert summary.last_day == date(2026, 10, 18)
    assert summary.row_count == 4


def test_the_unused_tube_is_kept_out_of_spend_and_off_its_buyers_opening() -> None:
    summary = sheet(
        (day(10, 11), "court", 14.8, "alice", 1, 1, 1, 1),
        (day(10, 18), "shuttle", 76.79, "frank", 0, 0, 0, None),  # nobody has used it
        (None, None, None, None),  # a blank row
    )

    assert summary.pending == PendingShuttle(
        row=3, day=date(2026, 10, 18), payer="frank", amount_cents=7679
    )
    assert summary.paid["frank"] == Fraction("76.79")  # Paid still counts it, like the sheet
    assert summary.spend["frank"] == Fraction("3.7")
    assert sum(map(summary.surplus, summary.players)) == Fraction("76.79")
    assert summary.opening_cents() == {"alice": 1110, "bob": -370, "carol": -370, "frank": -370}


def test_a_court_row_on_the_day_the_tube_was_bought_used_the_previous_one() -> None:
    summary = sheet(
        (day(10, 18), "court", 14.8, "alice", 1, 1, None, 1),
        (day(10, 18), "shuttle", 76.79, "frank", 0, 0, None),  # bought after that session
    )

    assert summary.pending is not None and summary.pending.row == 3


def test_refuses_an_unused_tube_that_later_sessions_played_with() -> None:
    with pytest.raises(SheetError, match=r"Row 2: this tube has no shares, but rows 3, 4 played"):
        sheet(
            (day(10, 10), "shuttle", 76.79, "frank", None, None, None),
            (day(10, 11), "court", 14.8, "alice", 1, 1, None, 1),
            (day(10, 18), "court", 14.8, "alice", 1, 1, None, 1),
        )
    with pytest.raises(SheetError, match=r"Row 2: .* needs a date"):
        sheet(
            (None, "shuttle", 76.79, "frank", None, None, None),
            (day(10, 11), "court", 14.8, "alice", 1, 1, None, 1),
        )


def test_opening_balances_are_whole_cents_that_sum_to_zero() -> None:
    summary = sheet(
        (day(10, 4), "court", 14.8, "alice", 1, 1, 1, None),  # 4.9333… each
        (day(10, 11), "court", 10, "bob", 1, 1, 1, None),  # 3.3333… each
    )

    cents = summary.opening_cents()

    # Exactly 653⅓, 173⅓ and −826⅔: all three are ⅓ over the floor, so the
    # leftover cent goes to the first column.
    assert cents == {"alice": 654, "bob": 173, "carol": -827}
    assert sum(cents.values()) == 0


def test_zero_sum_cents_gives_leftover_cents_to_the_largest_remainders() -> None:
    thirds = {"a": Fraction(1, 300), "b": Fraction(1, 300), "c": Fraction(-2, 300)}

    assert zero_sum_cents(thirds) == {"a": 1, "b": 0, "c": -1}  # tie goes to the earlier key
    with pytest.raises(ValueError, match="sum to zero"):
        zero_sum_cents({"a": Fraction(1, 100)})


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            [(day(1, 4), "racket", 10, "alice", 1)],
            r"Row 2: the item must be court, shuttle or transfer",
        ),
        ([(day(1, 4), "court", 0, "alice", 1)], r"Row 2: the price must be a positive amount"),
        ([(day(1, 4), "court", 1.234, "alice", 1)], r"Row 2: the price must be .* whole cents"),
        ([(day(1, 4), "court", "", "alice", 1)], r"Row 2: the price is empty"),
        ([(day(1, 4), "court", 14.8, "grace", 1)], r"Row 2: 'Paid By' must be one of"),
        ([(day(1, 4), "court", 14.8, "alice", "x")], r"Row 2: alice's share must be a number"),
        ([(day(1, 4), "court", 14.8, "alice", -1)], r"Row 2: alice's share can't be negative"),
        ([(day(1, 4), "court", 14.8, "alice")], r"Row 2: a court row needs at least one share"),
        ([(day(1, 4), "transfer", 5, "alice", None, 1, 1)], r"Row 2: a transfer needs exactly one"),
        ([(day(1, 4), "transfer", 5, "alice", 1)], r"Row 2: a transfer needs exactly one"),
        ([("soon", "court", 14.8, "alice", 1)], r"Row 2: the date must be a date"),
        ([(None, "court", 14.8, "alice", 1)], r"No row has a date"),
        (
            [
                (day(1, 4), "shuttle", 50, "alice"),
                (day(1, 11), "shuttle", 60, "carol", 1),
            ],
            r"Row 2: only the latest shuttle purchase may have no shares yet",
        ),
    ],
)
def test_rejects_rows_it_cannot_reproduce(rows: list[tuple[object, ...]], message: str) -> None:
    with pytest.raises(SheetError, match=message):
        sheet(*rows)


def test_needs_the_expected_columns() -> None:
    with pytest.raises(SheetError, match=r"Row 1: missing column\(s\) 'paid by'"):
        read_items(("Date", "Item", "Price", "Payer", "alice", "Number of Shares"), [])
    with pytest.raises(SheetError, match=r"Row 1: each column between"):
        read_items(("Date", "Item", "Price", "Paid By", None, "Number of Shares"), [])
