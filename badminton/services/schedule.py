"""The tick's steps, in the order of FR-OPS-2. Feature tasks add theirs here."""

from __future__ import annotations

from badminton.services.attendance import post_due_cards
from badminton.services.drafts import cleanup_expired_drafts
from badminton.services.finalize import finalize_ended_sessions
from badminton.services.monthly import monthly_invoice
from badminton.services.tick import TickStep


def default_steps() -> list[tuple[str, TickStep]]:
    return [
        ("attendance_cards", post_due_cards),
        ("finalize_sessions", finalize_ended_sessions),
        ("monthly_invoice", monthly_invoice),
        ("cleanup_drafts", cleanup_expired_drafts),
    ]
