"""The tick's steps, in the order of FR-OPS-2. Feature tasks add theirs here."""

from __future__ import annotations

from badminton.services.attendance import post_due_cards
from badminton.services.drafts import cleanup_expired_drafts
from badminton.services.tick import TickStep


def default_steps() -> list[tuple[str, TickStep]]:
    return [
        ("attendance_cards", post_due_cards),
        ("cleanup_drafts", cleanup_expired_drafts),
    ]
