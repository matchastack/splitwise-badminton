"""Money helpers. All amounts are integer cents (FR-LED-1, NFR-2)."""

from __future__ import annotations

import re
from collections.abc import Hashable, Mapping
from typing import Protocol

MAX_BOOKING_CENTS = 50_000  # $500.00, the FR-BK-6 limit

_AMOUNT_RE = re.compile(r"^(?:S?\$)?\s*(\d+)(?:\.(\d{1,2}))?$", re.IGNORECASE)


class _Sortable(Hashable, Protocol):
    def __lt__(self, other: object, /) -> bool: ...


def allocate[K: _Sortable](total_cents: int, weights: Mapping[K, int]) -> dict[K, int]:
    """Split ``total_cents`` in proportion to ``weights`` so the parts sum exactly to the total.

    Uses the largest-remainder method: everyone gets the floor of their exact share,
    then the leftover cents go one each to the largest remainders. Equal remainders
    are broken by ascending key, so the result is deterministic.

    >>> allocate(1480, {1: 1, 2: 1, 3: 1})
    {1: 494, 2: 493, 3: 493}
    """
    if total_cents < 0:
        raise ValueError("total_cents must not be negative")
    if any(weight < 0 for weight in weights.values()):
        raise ValueError("weights must not be negative")
    total_weight = sum(weights.values())
    if total_weight == 0:
        raise ValueError("weights must include at least one positive weight")

    shares: dict[K, int] = {}
    remainders: dict[K, int] = {}
    for key, weight in weights.items():
        shares[key], remainders[key] = divmod(total_cents * weight, total_weight)

    leftover = total_cents - sum(shares.values())
    by_largest_remainder = sorted(remainders, key=lambda key: (-remainders[key], key))
    for key in by_largest_remainder[:leftover]:
        shares[key] += 1
    return shares


def fmt_sgd(cents: int) -> str:
    """Format cents as dollars: ``1480`` -> ``$14.80``, ``-370`` -> ``-$3.70``."""
    sign = "-" if cents < 0 else ""
    dollars, remainder = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{remainder:02d}"


def fmt_signed(cents: int) -> str:
    """``500`` -> ``+$5.00``, ``-500`` -> ``−$5.00`` (with a typographic minus)."""
    return f"{'+' if cents >= 0 else '−'}{fmt_sgd(abs(cents))}"


def parse_amount(text: str, *, max_cents: int = MAX_BOOKING_CENTS) -> int:
    """Parse a typed dollar amount into cents: ``14.8``, ``$14.80`` and ``14`` are accepted."""
    match = _AMOUNT_RE.fullmatch(text.strip())
    if match is None:
        raise ValueError("Enter an amount like 14.80")
    dollars, fraction = match.groups()
    cents = int(dollars) * 100 + int((fraction or "0").ljust(2, "0"))
    if not 1 <= cents <= max_cents:
        raise ValueError(f"Enter an amount between $0.01 and {fmt_sgd(max_cents)}")
    return cents
