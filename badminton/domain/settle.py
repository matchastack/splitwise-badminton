"""Settle-up: the fewest transfers that bring balances back to about zero (FR-INV-2)."""

from __future__ import annotations

import heapq
from collections.abc import Mapping
from dataclasses import dataclass

MIN_TRANSFER_CENTS = 100


@dataclass(frozen=True, slots=True)
class Payment:
    from_id: int
    to_id: int
    amount_cents: int


def simplify_debts(
    balances: Mapping[int, int], min_cents: int = MIN_TRANSFER_CENTS
) -> list[Payment]:
    """Greedily match the biggest debtor with the biggest creditor until everyone is square.

    Balances use the ledger's sign: negative means the member owes the group. Payments
    under ``min_cents`` are left out; those amounts simply carry forward in the balances.
    Ties are broken by member ID, so the result is deterministic.
    """
    if sum(balances.values()) != 0:
        raise ValueError("balances must sum to zero")

    # Max-heaps of (-amount, member_id): biggest amount first, then lowest ID.
    debtors = [(cents, member_id) for member_id, cents in balances.items() if cents < 0]
    creditors = [(-cents, member_id) for member_id, cents in balances.items() if cents > 0]
    heapq.heapify(debtors)
    heapq.heapify(creditors)

    payments: list[Payment] = []
    while debtors and creditors:
        debt, debtor = heapq.heappop(debtors)
        credit, creditor = heapq.heappop(creditors)
        amount = min(-debt, -credit)
        if amount >= min_cents:
            payments.append(Payment(debtor, creditor, amount))
        if (debt := debt + amount) < 0:
            heapq.heappush(debtors, (debt, debtor))
        if (credit := credit + amount) < 0:
            heapq.heappush(creditors, (credit, creditor))
    return payments
