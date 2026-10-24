import sys
from datetime import date
from pathlib import Path

from badminton.domain.screenshot import read_booking
from badminton.domain.timeutil import at_sgt
from badminton.services.ocr import read_text

SCREENSHOT = Path(__file__).resolve().parents[1] / "fixtures" / "activesg_booking.jpg"


def test_reads_an_activesg_screenshot() -> None:
    """The screenshot as Telegram delivers it: a 590×1280 JPEG."""
    found = read_booking(read_text(SCREENSHOT.read_bytes()), date(2026, 9, 30))

    assert (found.venue, found.courts) == ("Hougang Sport Hall", "11")
    assert (found.starts_at, found.minutes) == (at_sgt(date(2026, 10, 4), 11), 120)


def test_anything_but_an_image_reads_as_nothing() -> None:
    assert read_text(b"not an image") == ""
    assert read_text(b"") == ""


def test_a_tesseract_that_will_not_load_reads_as_nothing(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "tesserocr", None)  # importing it raises ImportError

    assert read_text(SCREENSHOT.read_bytes()) == ""
