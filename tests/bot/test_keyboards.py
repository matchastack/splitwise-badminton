from datetime import date

import pytest

from badminton.bot.keyboards import (
    Callback,
    calendar_keyboard,
    decode,
    encode,
    hour_keyboard,
    parse_day,
    parse_month,
)

TODAY = date(2026, 10, 21)


def texts(markup) -> list[list[str]]:
    return [[button.text for button in row] for row in markup.inline_keyboard]


def callbacks(markup) -> list[list[str]]:
    return [[button.callback_data for button in row] for row in markup.inline_keyboard]


def test_calendar_layout_for_this_month() -> None:
    markup = calendar_keyboard("bk", 7, date(2026, 10, 1), TODAY)
    rows = texts(markup)

    assert rows[0] == [" ", "Oct 2026", "›"]  # can't go back before this month
    assert rows[1] == ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    assert rows[2] == [" ", " ", " ", "·", "·", "·", "·"]  # 1 Oct 2026 is a Thursday
    assert rows[4][:2] == ["·", "·"]  # 12 and 13 Oct are past
    assert rows[5] == ["·", "·", "21", "22", "23", "24", "25"]
    assert rows[-1] == ["26", "27", "28", "29", "30", "31", " "]
    assert callbacks(markup)[5][2] == "bk:7:d:20261021"
    assert callbacks(markup)[0][2] == "bk:7:m:202611"
    assert callbacks(markup)[2][3] == "bk:7:noop"


def test_calendar_navigation_limits() -> None:
    december = calendar_keyboard("bk", 7, date(2026, 12, 1), TODAY)
    last = calendar_keyboard("bk", 7, date(2027, 4, 1), TODAY)

    assert callbacks(december)[0][0] == "bk:7:m:202611"
    assert callbacks(december)[0][2] == "bk:7:m:202701"  # rolls over the year
    assert texts(last)[0] == ["‹", "Apr 2027", " "]  # six months ahead is the limit
    assert "·" not in [text for row in texts(december)[2:] for text in row]


def test_hour_keyboard() -> None:
    rows = texts(hour_keyboard("bk", 7))

    assert rows[0] == ["07:00", "08:00", "09:00", "10:00"]
    assert rows[3] == ["19:00", "20:00", "21:00", "22:00"]
    assert rows[4] == ["‹ Change date"]
    assert callbacks(hour_keyboard("bk", 7))[0][0] == "bk:7:h:7"


def test_callback_round_trip_and_size() -> None:
    data = encode("bk", 2_147_483_647, "d", "20261231")

    assert len(data.encode()) <= 64
    assert decode(data) == Callback("bk", 2_147_483_647, "d", "20261231")
    assert decode("bk:3:back") == Callback("bk", 3, "back", "")


@pytest.mark.parametrize(("value", "expected"), [("20261004", date(2026, 10, 4))])
def test_parse_day(value: str, expected: date) -> None:
    assert parse_day(value) == expected


def test_parse_month() -> None:
    assert parse_month("202701") == date(2027, 1, 1)
