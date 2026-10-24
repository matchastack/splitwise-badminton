import random
from collections import Counter

import pytest

from badminton.domain.settle import Payment, simplify_debts

ALICE, BOB, CAROL, DAVE, FRANK = 1, 2, 3, 4, 8


def test_requirements_example() -> None:
    """FR-INV-2: alice +$45, frank +$10, bob −$30, carol −$20, dave −$5."""
    balances = {ALICE: 4500, FRANK: 1000, BOB: -3000, CAROL: -2000, DAVE: -500}

    assert simplify_debts(balances) == [
        Payment(BOB, ALICE, 3000),
        Payment(CAROL, ALICE, 1500),
        Payment(CAROL, FRANK, 500),
        Payment(DAVE, FRANK, 500),
    ]


def test_nothing_to_settle() -> None:
    assert simplify_debts({}) == []
    assert simplify_debts({ALICE: 0, BOB: 0}) == []


def test_small_amounts_carry_forward() -> None:
    balances = {ALICE: 2050, BOB: -2000, CAROL: -50}

    assert simplify_debts(balances) == [Payment(BOB, ALICE, 2000)]


def test_rejects_unbalanced_input() -> None:
    with pytest.raises(ValueError):
        simplify_debts({ALICE: 100, BOB: -99})


def test_random_balances_settle_with_few_transfers() -> None:
    rng = random.Random(42)
    for _ in range(500):
        members = rng.sample(range(1, 40), rng.randint(2, 12))
        balances = {member: rng.randint(-20_000, 20_000) for member in members[:-1]}
        balances[members[-1]] = -sum(balances.values())
        min_cents = rng.choice([1, 100])

        payments = simplify_debts(balances, min_cents)

        moved: Counter[int] = Counter()
        for payment in payments:
            assert payment.amount_cents >= min_cents
            assert balances[payment.from_id] < 0 < balances[payment.to_id]
            moved[payment.from_id] += payment.amount_cents
            moved[payment.to_id] -= payment.amount_cents
        residual = {member: balances[member] + moved[member] for member in balances}
        if min_cents == 1:
            assert all(cents == 0 for cents in residual.values())
            assert len(payments) <= len([b for b in balances.values() if b]) - 1
        # No one is pushed past zero: debtors stay ≤ 0 and creditors ≥ 0.
        assert all(
            (cents <= 0) == (balances[m] <= 0) or cents == 0 for m, cents in residual.items()
        )
        assert payments == simplify_debts(dict(reversed(list(balances.items()))), min_cents)
