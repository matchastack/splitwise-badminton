from datetime import UTC, date, datetime

import pytest

from badminton.domain.timeutil import (
    SGT,
    at_sgt,
    card_due_date,
    fmt_date,
    fmt_hours,
    fmt_period,
    fmt_session,
    fmt_time,
    is_card_due,
    month_bounds,
    parse_hours,
    period_of,
    previous_period,
    sgt_date,
    to_sgt,
)

SESSION_START = at_sgt(date(2026, 10, 4), 11)  # Sun 4 Oct, 11:00 SGT


def test_sgt_conversion() -> None:
    late_utc = datetime(2026, 10, 31, 16, 30, tzinfo=UTC)  # 00:30 on 1 Nov in Singapore

    assert to_sgt(late_utc).hour == 0
    assert sgt_date(late_utc) == date(2026, 11, 1)
    assert SESSION_START.astimezone(UTC) == datetime(2026, 10, 4, 3, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        to_sgt(datetime(2026, 10, 4, 11, 0))


@pytest.mark.parametrize(
    ("moment", "period", "previous"),
    [
        (datetime(2026, 10, 31, 15, 59, tzinfo=UTC), "2026-10", "2026-09"),
        (datetime(2026, 10, 31, 16, 0, tzinfo=UTC), "2026-11", "2026-10"),
        (at_sgt(date(2027, 1, 1), 10), "2027-01", "2026-12"),
    ],
)
def test_periods_follow_sgt_months(moment: datetime, period: str, previous: str) -> None:
    assert period_of(moment) == period
    assert previous_period(moment) == previous


@pytest.mark.parametrize(
    ("period", "start", "end"),
    [
        ("2026-10", at_sgt(date(2026, 10, 1), 0), at_sgt(date(2026, 11, 1), 0)),
        ("2026-12", at_sgt(date(2026, 12, 1), 0), at_sgt(date(2027, 1, 1), 0)),
    ],
)
def test_month_bounds(period: str, start: datetime, end: datetime) -> None:
    assert month_bounds(period) == (start, end)


@pytest.mark.parametrize("period", ["2026-13", "2026-1", "Oct 2026"])
def test_month_bounds_rejects_bad_period(period: str) -> None:
    with pytest.raises(ValueError):
        month_bounds(period)


def test_fmt_period() -> None:
    assert fmt_period("2026-10") == "October 2026"


def test_card_is_due_from_the_sgt_date_two_days_before() -> None:
    assert card_due_date(SESSION_START, 2) == date(2026, 10, 2)
    # 23:59 SGT on Thu 1 Oct: not yet. 00:00 SGT on Fri 2 Oct onwards: due, including catch-up.
    assert not is_card_due(SESSION_START, at_sgt(date(2026, 10, 1), 23, 59), 2)
    assert is_card_due(SESSION_START, at_sgt(date(2026, 10, 2), 0), 2)
    assert is_card_due(SESSION_START, at_sgt(date(2026, 10, 3), 10), 2)


def test_formatting() -> None:
    end = at_sgt(date(2026, 10, 4), 13)

    assert fmt_date(date(2026, 10, 4)) == "Sun 4 Oct"
    assert fmt_time(SESSION_START.astimezone(UTC)) == "11:00"
    assert fmt_session(SESSION_START, end) == "Sun 4 Oct, 11:00–13:00"
    assert SESSION_START.tzinfo is SGT


@pytest.mark.parametrize(("minutes", "text"), [(60, "1h"), (120, "2h"), (90, "1.5h"), (30, "0.5h")])
def test_fmt_hours(minutes: int, text: str) -> None:
    assert fmt_hours(minutes) == text


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("3", 180),
        ("1.5", 90),
        ("2h", 120),
        ("2 hours", 120),
        (" 0.5 ", 30),
        ("6", 360),
        ("3HRS", 180),
    ],
)
def test_parse_hours(text: str, minutes: int) -> None:
    assert parse_hours(text) == minutes


@pytest.mark.parametrize("text", ["", "two", "1.25", "0", "6.5", "-1", "2 days"])
def test_parse_hours_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        parse_hours(text)
