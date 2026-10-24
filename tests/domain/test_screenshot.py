from datetime import date

import pytest

from badminton.domain.screenshot import (
    ScreenshotBooking,
    fmt_courts,
    parse_courts,
    parse_venue,
    read_booking,
)
from badminton.domain.timeutil import at_sgt

TODAY = date(2026, 9, 30)

# What Tesseract reads off the screenshot in tests/fixtures/activesg_booking.jpg.
OCR_TEXT = """12:03 94 at 56 GD:

@ A Singapore Government Agency Website How to identify v Cs)
EI

&

Booking confirmed!

BOOKING DETAILS
What Badminton

Where Hougang Sport Hall
93 Hougang Avenue 4 Singapore
538832
When Sun, 4 Oct, 11:00 am
Court 11
Sun, 4 Oct, 12:00 pm
Court 11

Back to Home

vie)

activesg.gov.sg @

< o om B&B
"""


def slots(*lines: str) -> str:
    return "Where Hougang Sport Hall\nWhen " + "\n".join(lines)


def test_reads_the_screenshot() -> None:
    assert read_booking(OCR_TEXT, TODAY) == ScreenshotBooking(
        venue="Hougang Sport Hall",
        starts_at=at_sgt(date(2026, 10, 4), 11),
        minutes=120,
        courts="11",
        court_minutes=120,
    )


@pytest.mark.parametrize(
    ("time", "hour"),
    [("12:00 am", 0), ("12:00 pm", 12), ("1:00 pm", 13), ("9:00 PM", 21), ("7:00am", 7)],
)
def test_twelve_hour_times(time: str, hour: int) -> None:
    found = read_booking(slots(f"Sun, 4 Oct, {time}", "Court 1"), TODAY)

    assert found.starts_at == at_sgt(date(2026, 10, 4), hour)


@pytest.mark.parametrize(
    "line",
    [
        "Sun, 4 Oct, 11.00 am",
        "Sun 4 Oct 11:00am",
        "Sunday, 4 October, 11:00 a.m.",
        "4 Oct, 11:00 am",
    ],
)
def test_tolerates_what_ocr_makes_of_the_time(line: str) -> None:
    assert read_booking(slots(line, "Court 11"), TODAY).starts_at == at_sgt(date(2026, 10, 4), 11)


@pytest.mark.parametrize(
    ("line", "today", "expected"),
    [
        ("Sun, 3 Jan, 9:00 am", date(2026, 12, 28), date(2027, 1, 3)),  # into the new year
        ("Wed, 30 Sept, 9:00 am", TODAY, TODAY),  # later today
        ("Mon, 5 Oct, 9:00 am", TODAY, date(2026, 10, 5)),
        ("5 Oct, 9:00 am", TODAY, date(2026, 10, 5)),  # no weekday read
    ],
)
def test_the_date_is_its_next_occurrence(line: str, today: date, expected: date) -> None:
    assert read_booking(slots(line, "Court 2"), today).starts_at == at_sgt(expected, 9)


@pytest.mark.parametrize(
    ("line", "today"),
    [
        ("Mon, 1 Nov, 9:00 am", date(2026, 10, 24)),  # 1 Nov 2026 is a Sunday: misread
        ("Wed, 5 Oct, 9:00 am", TODAY),  # likewise; not moved to a year it fits
        ("Wed, 30 Dec, 9:00 am", date(2027, 1, 2)),  # passed
        ("Fri, 5 Sept, 9:00 am", TODAY),  # passed; next year's is too far ahead
        ("Thu, 1 Jul, 9:00 am", TODAY),  # over six months ahead
    ],
)
def test_a_date_that_does_not_fit_is_left_out(line: str, today: date) -> None:
    found = read_booking(slots(line, "Court 2"), today)

    assert (found.starts_at, found.minutes, found.court_minutes) == (None, None, None)
    assert found.courts == "2"


def test_two_courts_at_once() -> None:
    found = read_booking(
        slots("Sun, 4 Oct, 11:00 am", "Court 11", "Sun, 4 Oct, 11:00 am", "Court 12"), TODAY
    )

    assert (found.starts_at, found.minutes) == (at_sgt(date(2026, 10, 4), 11), 60)
    assert (found.courts, found.court_minutes) == ("11, 12", 120)


def test_changing_court_halfway() -> None:
    found = read_booking(
        slots("Sun, 4 Oct, 11:00 am", "Court 11", "Sun, 4 Oct, 12:00 pm", "Court 3"), TODAY
    )

    assert (found.minutes, found.courts, found.court_minutes) == (120, "11, 3", 120)


def test_court_time_counts_each_slot_once() -> None:
    three_court_hours = (
        slots("Sun, 4 Oct, 11:00 am", "Court 11", "Sun, 4 Oct, 12:00 pm", "Court 11")
        + "\nSun, 4 Oct, 11:00 am\nCourt 12"
    )
    read_twice = (
        slots("Sun, 4 Oct, 11:00 am", "Court 11", "Sun, 4 Oct, 11:00 am", "Sun, 4 Oct, 12:00 pm")
        + "\nCourt 11"
    )

    assert read_booking(three_court_hours, TODAY).court_minutes == 180
    assert read_booking(read_twice, TODAY).court_minutes == 120  # the same slot, misread


def test_other_dates_on_the_page_are_not_slots() -> None:
    page = (
        "Booked on Thu, 1 Oct, 3:15 pm\n"
        + slots("Sun, 4 Oct, 11:00 am", "Court 11", "Sun, 4 Oct, 12:00 pm", "Court 11")
        + "\nNOTIFICATION\nReminder sent Sat, 3 Oct, 9:05 am"
    )

    found = read_booking(page, TODAY)

    assert (found.starts_at, found.minutes) == (at_sgt(date(2026, 10, 4), 11), 120)


def test_a_court_is_on_its_slots_line_or_the_next() -> None:
    no_court_line = slots("Sun, 4 Oct, 11:00 am", "", "Back to Home", "Book more Courts")
    same_line = slots("Sun, 4 Oct, 11:00 am Court 7")
    courtesy = slots("Sun, 4 Oct, 11:00 am", "Courtesy of ActiveSG")

    assert read_booking(no_court_line, TODAY).courts is None
    assert read_booking(same_line, TODAY).courts == "7"
    assert read_booking(courtesy, TODAY).courts is None


@pytest.mark.parametrize(
    "lines",
    [
        ("Sun, 4 Oct, 9:00 am", "Court 1", "Sun, 4 Oct, 3:00 pm", "Court 1"),  # a gap
        ("Sun, 4 Oct, 11:00 pm", "Court 1", "Mon, 5 Oct, 12:00 am", "Court 1"),  # two days
        ("Sun, 4 Oct, 11:30 am", "Court 1"),  # not on the hour
        tuple(f"Sun, 4 Oct, {hour}:00 am" for hour in range(5, 12)),  # over 6 hours
    ],
)
def test_time_is_left_out_unless_one_block_of_play(lines: tuple[str, ...]) -> None:
    found = read_booking(slots(*lines), TODAY)

    assert (found.starts_at, found.minutes) == (None, None)
    assert found.venue == "Hougang Sport Hall"
    assert found.courts in ("1", None)


def test_unreadable_text() -> None:
    assert read_booking("", TODAY) == ScreenshotBooking()
    assert read_booking("Cat pictures\n31 Feb, 25:00 pm", TODAY) == ScreenshotBooking()


def test_venue_on_its_own_line_or_missing() -> None:
    assert read_booking("Where\nBishan  Sport Hall\nWhen", TODAY).venue == "Bishan Sport Hall"
    assert read_booking("Where\nWhen Sun, 4 Oct, 11:00 am", TODAY).venue is None
    assert read_booking("Where :\n\nWhen", TODAY).venue is None


def test_a_slot_without_a_court() -> None:
    found = read_booking(slots("Sun, 4 Oct, 11:00 am"), TODAY)

    assert (found.minutes, found.courts) == (60, None)


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("11", "11"),
        ("Court 11", "11"),
        ("courts 11, 12", "11, 12"),
        ("5 and 6", "5, 6"),
        ("a", "A"),
    ],
)
def test_parse_courts(typed: str, stored: str) -> None:
    assert parse_courts(typed) == stored


@pytest.mark.parametrize("typed", ["", "court", "12345", "1 2 3 4 5 6 7", "11!"])
def test_parse_courts_refuses(typed: str) -> None:
    with pytest.raises(ValueError, match="Enter the court number"):
        parse_courts(typed)


def test_fmt_courts() -> None:
    assert (fmt_courts("11"), fmt_courts("11, 12")) == ("Court 11", "Courts 11, 12")


def test_parse_venue() -> None:
    assert parse_venue("  Bishan \n Sport Hall ") == "Bishan Sport Hall"
    with pytest.raises(ValueError, match="Enter the venue"):
        parse_venue("   ")
    with pytest.raises(ValueError, match="64 characters"):
        parse_venue("x" * 65)
