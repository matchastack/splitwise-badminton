"""Booking details read from the text of an ActiveSG booking screenshot (FR-BK-12).

The confirmation page lists the venue under "Where" and each one-hour slot under
"When", as its date and start time followed by its court::

    Where  Hougang Sport Hall
           93 Hougang Avenue 4 Singapore 538832
    When   Sun, 4 Oct, 11:00 am
           Court 11
           Sun, 4 Oct, 12:00 pm
           Court 11

The page shows no year: it is the date's next occurrence, which must fall on the
weekday shown. Whatever can't be read is left out, for the member to fill in. The
venue and courts a member types are cleaned the same way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import pairwise

from badminton.domain.timeutil import MAX_SESSION_MINUTES, at_sgt, sgt_date

SLOT_MINUTES = 60
MAX_VENUE_LENGTH = 64
MAX_COURTS = 6
BOOKABLE_DAYS_AHEAD = 183  # about the six months the booking calendar offers

_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
# "Sun, 4 Oct, 11:00 am", tolerating what OCR makes of it: "11.00am", "Sept", no weekday.
_SLOT_RE = re.compile(
    rf"\b(?:({'|'.join(_WEEKDAYS)})[a-z]*[,.]?\s+)?"
    rf"(\d{{1,2}})\s+({'|'.join(_MONTHS)})[a-z]*[,.]?\s+"
    r"(\d{1,2})[:.](\d{2})\s*([ap])\.?\s*m\b",
    re.IGNORECASE,
)
_COURT_RE = re.compile(r"\bcourt\b\s*[:#]?\s*([a-z0-9]{1,4})\b", re.IGNORECASE)
_WHERE_RE = re.compile(r"^where\b[\s:]*(.*)$", re.IGNORECASE)
_WHEN_RE = re.compile(r"^when\b[\s:]*", re.IGNORECASE)
_COURT_LABEL_RE = re.compile(r"[A-Z0-9]{1,4}")


@dataclass(frozen=True, slots=True)
class ScreenshotBooking:
    """What could be read. The time is only filled in when the slots are one block of play."""

    venue: str | None = None
    starts_at: datetime | None = None
    minutes: int | None = None
    courts: str | None = None  # as stored: "11", or "11, 12"
    court_minutes: int | None = None  # court time paid for: 240 for two courts for 2 hours


def read_booking(text: str, today: date) -> ScreenshotBooking:
    """The booking in a screenshot's text. ``today`` (SGT) settles the year."""
    slots = _slots(text, today)
    block = _block([starts_at for starts_at, _ in slots])
    courts = list(dict.fromkeys(court for _, court in slots if court))[:MAX_COURTS]
    # One slot is one court for an hour. A slot read without its court isn't another one.
    with_court = {starts_at for starts_at, court in slots if court}
    paid = {
        (starts_at, court) for starts_at, court in slots if court or starts_at not in with_court
    }
    return ScreenshotBooking(
        venue=_venue(text),
        starts_at=block[0] if block else None,
        minutes=block[1] if block else None,
        courts=", ".join(courts) or None,
        court_minutes=SLOT_MINUTES * len(paid) if block else None,
    )


def _venue(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if (match := _WHERE_RE.match(line)) is None:
            continue
        value = match.group(1)
        if not value and index + 1 < len(lines) and not lines[index + 1].lower().startswith("when"):
            value = lines[index + 1]  # the label on a line of its own
        value = re.sub(r"\s+", " ", value).strip()[:MAX_VENUE_LENGTH].strip()
        return value if re.search(r"[A-Za-z]{2}", value) else None
    return None


def _slots(text: str, today: date) -> list[tuple[datetime | None, str | None]]:
    """Each slot's start and court, in the order listed. A start is None when its date
    can't be trusted. A court is on its slot's line or the next one."""
    slots: list[tuple[datetime | None, str | None]] = []
    court_due = False
    for line in _when_lines(text):
        if (match := _SLOT_RE.search(line)) is not None:
            court = _COURT_RE.search(line, match.end())
            slots.append((_start(match, today), court.group(1).upper() if court else None))
            court_due = court is None
        elif court_due and (court := _COURT_RE.search(line)) is not None:
            slots[-1] = (slots[-1][0], court.group(1).upper())
            court_due = False
        elif line.strip():
            court_due = False
    return slots


def _when_lines(text: str) -> list[str]:
    """The "When" list: from its label to the first line that is neither a slot nor a
    court, so other dates on the page (a payment time, a banner) aren't taken for slots.
    Without the label, every line."""
    lines = text.splitlines()
    first = next((i for i, line in enumerate(lines) if _WHEN_RE.match(line.strip())), None)
    if first is None:
        return lines
    listed = [_WHEN_RE.sub("", lines[first].strip(), count=1)]
    for line in lines[first + 1 :]:
        if line.strip() and not _SLOT_RE.search(line) and not _COURT_RE.search(line):
            break
        listed.append(line)
    return listed


def _start(match: re.Match[str], today: date) -> datetime | None:
    weekday, day, month, hour, minute, half = match.groups()
    hour, minute = int(hour), int(minute)
    if not 1 <= hour <= 12 or minute > 59:
        return None
    on = _resolve_date(
        int(day),
        _MONTHS.index(month.lower()) + 1,
        _WEEKDAYS.index(weekday.lower()) if weekday else None,
        today,
    )
    return at_sgt(on, hour % 12 + (12 if half.lower() == "p" else 0), minute) if on else None


def _resolve_date(day: int, month: int, weekday: int | None, today: date) -> date | None:
    """The next ``day``/``month`` from ``today`` on, if it is within the months bookings
    are made for and falls on ``weekday``. Otherwise the day or the weekday was misread,
    or the booking has passed: None, so the member picks the date."""
    for year in (today.year, today.year + 1):
        try:
            on = date(year, month, day)
        except ValueError:  # 29 Feb, or a misread day
            continue
        if on >= today:
            break
    else:
        return None
    if (on - today).days > BOOKABLE_DAYS_AHEAD:
        return None
    return on if weekday is None or on.weekday() == weekday else None


def _block(starts: list[datetime | None]) -> tuple[datetime, int] | None:
    """The start and length of slots that run back to back on one day, on the hour."""
    if None in starts:
        return None
    starts = sorted(set(starts))
    if not starts or any(start.minute for start in starts):
        return None
    if len({sgt_date(start) for start in starts}) > 1:
        return None
    if any(
        later - earlier != timedelta(minutes=SLOT_MINUTES) for earlier, later in pairwise(starts)
    ):
        return None
    minutes = SLOT_MINUTES * len(starts)
    return (starts[0], minutes) if minutes <= MAX_SESSION_MINUTES else None


# Typed corrections ------------------------------------------------------------


def parse_venue(text: str) -> str:
    venue = re.sub(r"\s+", " ", text).strip()
    if not venue:
        raise ValueError("Enter the venue, e.g. Hougang Sport Hall")
    if len(venue) > MAX_VENUE_LENGTH:
        raise ValueError(f"Keep the venue to {MAX_VENUE_LENGTH} characters")
    return venue


def parse_courts(text: str) -> str:
    """Typed courts as stored: ``Court 11`` -> ``11``, ``11 and 12`` -> ``11, 12``."""
    words = re.sub(r"\bcourts?\b|\band\b|[,&/+]", " ", text, flags=re.IGNORECASE).split()
    labels = list(dict.fromkeys(word.upper() for word in words))
    if not 1 <= len(labels) <= MAX_COURTS or not all(
        _COURT_LABEL_RE.fullmatch(label) for label in labels
    ):
        raise ValueError("Enter the court number, e.g. 11, or 11, 12 for two courts")
    return ", ".join(labels)


def fmt_courts(courts: str) -> str:
    """``11`` -> ``Court 11``, ``11, 12`` -> ``Courts 11, 12``."""
    return f"Courts {courts}" if "," in courts else f"Court {courts}"
