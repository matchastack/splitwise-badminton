"""Singapore time helpers (NFR-7). Singapore has no daylight saving, so a fixed
UTC+8 offset is exact and avoids depending on the system's timezone database.

Timestamps are stored as aware UTC datetimes; everything shown to users is SGT.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta, timezone

SGT = timezone(timedelta(hours=8), "SGT")

MAX_SESSION_MINUTES = 6 * 60
_HOURS_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)?$", re.IGNORECASE)
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def utc_now() -> datetime:
    """The only place that reads the clock; everything else takes ``now`` as an argument."""
    return datetime.now(UTC)


def to_sgt(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("expected a timezone-aware datetime")
    return moment.astimezone(SGT)


def sgt_date(moment: datetime) -> date:
    return to_sgt(moment).date()


def at_sgt(day: date, hour: int, minute: int = 0) -> datetime:
    """``day`` at ``hour:minute`` Singapore time."""
    return datetime.combine(day, time(hour, minute), tzinfo=SGT)


# Months ---------------------------------------------------------------------


def period_of(moment: datetime) -> str:
    """The SGT month containing ``moment``, as ``YYYY-MM``."""
    local = to_sgt(moment)
    return f"{local.year:04d}-{local.month:02d}"


def previous_period(moment: datetime) -> str:
    """The SGT month before the one containing ``moment``: what an invoice on the 1st covers."""
    first = to_sgt(moment).date().replace(day=1)
    return period_of(at_sgt(first - timedelta(days=1), 12))


def month_bounds(period: str) -> tuple[datetime, datetime]:
    """Start (inclusive) and end (exclusive) of an SGT month, e.g. ``"2026-10"``."""
    year, month = _parse_period(period)
    start = at_sgt(date(year, month, 1), 0)
    following = date(year + month // 12, month % 12 + 1, 1)
    return start, at_sgt(following, 0)


def fmt_period(period: str) -> str:
    """``"2026-10"`` -> ``"October 2026"``."""
    year, month = _parse_period(period)
    return f"{_MONTH_NAMES[month - 1]} {year}"


def _parse_period(period: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-(\d{2})", period)
    if match is None or not 1 <= int(match.group(2)) <= 12:
        raise ValueError(f"expected YYYY-MM, got {period!r}")
    return int(match.group(1)), int(match.group(2))


# Attendance card timing (FR-ATT-1) ------------------------------------------


def card_due_date(session_start: datetime, days_before: int) -> date:
    return sgt_date(session_start) - timedelta(days=days_before)


def is_card_due(session_start: datetime, now: datetime, days_before: int) -> bool:
    """Due from the SGT calendar date ``days_before`` the session, so a missed run catches up."""
    return sgt_date(now) >= card_due_date(session_start, days_before)


# Formatting -----------------------------------------------------------------


def fmt_date(day: date) -> str:
    """``Sun 4 Oct``."""
    return f"{_WEEKDAYS[day.weekday()]} {day.day} {_MONTHS[day.month - 1]}"


def fmt_time(moment: datetime) -> str:
    """``11:00`` in SGT."""
    return to_sgt(moment).strftime("%H:%M")


def fmt_session(start: datetime, end: datetime) -> str:
    """``Sun 4 Oct, 11:00–13:00`` in SGT."""
    return f"{fmt_date(sgt_date(start))}, {fmt_time(start)}–{fmt_time(end)}"


def minutes_between(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() // 60)


def fmt_hours(minutes: int) -> str:
    """``120`` -> ``2h``, ``90`` -> ``1.5h``."""
    hours = minutes / 60
    return f"{hours:g}h"


def parse_hours(text: str) -> int:
    """Parse a typed duration into minutes: ``3``, ``1.5``, ``2h`` or ``2 hours``.

    Durations are in 0.5-hour steps, up to 6 hours (FR-BK-5).
    """
    match = _HOURS_RE.fullmatch(text.strip())
    if match is None:
        raise ValueError("Enter the hours as a number, e.g. 3 or 1.5")
    half_hours = float(match.group(1)) * 2
    if half_hours != int(half_hours):
        raise ValueError("Use steps of half an hour, e.g. 1.5")
    minutes = int(half_hours) * 30
    if not 30 <= minutes <= MAX_SESSION_MINUTES:
        raise ValueError(f"Enter between 0.5 and {MAX_SESSION_MINUTES // 60} hours")
    return minutes
