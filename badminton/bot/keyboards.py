"""Inline keyboards for picking a date and a start hour (FR-BK-3, FR-BK-4).

Callback data is ``<kind>:<draft id>:<action>[:<value>]``, well under Telegram's
64-byte limit (constraint C-4).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

FIRST_HOUR = 7
LAST_HOUR = 22
MONTHS_AHEAD = 6

NOOP = "noop"
PICK_DATE = "d"
SHOW_MONTH = "m"
PICK_HOUR = "h"
BACK = "back"

_WEEKDAYS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True, slots=True)
class Callback:
    kind: str
    draft_id: int
    action: str
    value: str = ""


def encode(kind: str, draft_id: int, action: str, value: str = "") -> str:
    data = f"{kind}:{draft_id}:{action}" + (f":{value}" if value else "")
    assert len(data.encode()) <= 64, data
    return data


def decode(data: str) -> Callback:
    kind, draft_id, action, *rest = data.split(":", 3)
    return Callback(kind, int(draft_id), action, rest[0] if rest else "")


def month_key(month: date) -> str:
    return f"{month.year:04d}{month.month:02d}"


def parse_month(value: str) -> date:
    return date(int(value[:4]), int(value[4:6]), 1)


def parse_day(value: str) -> date:
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def calendar_keyboard(kind: str, draft_id: int, month: date, today: date) -> InlineKeyboardMarkup:
    """A month grid with past days disabled and ‹ › to change month (from this month up
    to ``MONTHS_AHEAD`` ahead)."""
    month = month.replace(day=1)
    this_month = today.replace(day=1)

    def button(text: str, action: str = NOOP, value: str = "") -> InlineKeyboardButton:
        return InlineKeyboardButton(text, callback_data=encode(kind, draft_id, action, value))

    previous_month = _add_months(month, -1)
    next_month = _add_months(month, 1)
    can_go_back = previous_month >= this_month
    can_go_forward = next_month <= _add_months(this_month, MONTHS_AHEAD)
    rows = [
        [
            button("‹", SHOW_MONTH, month_key(previous_month)) if can_go_back else button(" "),
            button(f"{_MONTHS[month.month - 1]} {month.year}"),
            button("›", SHOW_MONTH, month_key(next_month)) if can_go_forward else button(" "),
        ],
        [button(day) for day in _WEEKDAYS],
    ]
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(month.year, month.month):
        row = []
        for day in week:
            if day.month != month.month:
                row.append(button(" "))
            elif day < today:
                row.append(button("·"))
            else:
                row.append(button(str(day.day), PICK_DATE, day.strftime("%Y%m%d")))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def hour_keyboard(kind: str, draft_id: int) -> InlineKeyboardMarkup:
    """Start hours 07:00–22:00, four per row, with a way back to the calendar."""
    hours = [
        InlineKeyboardButton(
            f"{hour:02d}:00", callback_data=encode(kind, draft_id, PICK_HOUR, str(hour))
        )
        for hour in range(FIRST_HOUR, LAST_HOUR + 1)
    ]
    rows = [hours[i : i + 4] for i in range(0, len(hours), 4)]
    rows.append([InlineKeyboardButton("‹ Change date", callback_data=encode(kind, draft_id, BACK))])
    return InlineKeyboardMarkup(rows)


def _add_months(month: date, delta: int) -> date:
    index = month.year * 12 + month.month - 1 + delta
    return date(index // 12, index % 12 + 1, 1)
