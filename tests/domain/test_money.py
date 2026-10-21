import random

import pytest

from badminton.domain.money import allocate, fmt_sgd, parse_amount


@pytest.mark.parametrize(
    ("total", "weights", "expected"),
    [
        (1480, {1: 1, 2: 1, 3: 1, 4: 1}, {1: 370, 2: 370, 3: 370, 4: 370}),
        (1480, {1: 1, 2: 1, 3: 1}, {1: 494, 2: 493, 3: 493}),
        (1480, {3: 1, 1: 1, 2: 1}, {3: 493, 1: 494, 2: 493}),  # ties by key, not insertion
        (100, {1: 1, 2: 2}, {1: 33, 2: 67}),  # 33.33 / 66.67: the larger remainder wins
        # The sheet's 16 Aug 2026 batch: $80 over 50 hours, weights in minutes.
        (
            8000,
            {1: 720, 2: 600, 3: 120, 4: 720, 5: 720, 6: 120},
            {1: 1920, 2: 1600, 3: 320, 4: 1920, 5: 1920, 6: 320},
        ),
        (500, {1: 0, 2: 3}, {1: 0, 2: 500}),
        (0, {1: 1, 2: 1}, {1: 0, 2: 0}),
    ],
)
def test_allocate_examples(total: int, weights: dict[int, int], expected: dict[int, int]) -> None:
    assert allocate(total, weights) == expected


def test_allocate_is_exact_fair_and_deterministic() -> None:
    rng = random.Random(20261021)
    for _ in range(2000):
        weights = {key: rng.randint(0, 600) for key in rng.sample(range(1, 50), rng.randint(1, 9))}
        if not any(weights.values()):
            continue
        total = rng.randint(0, 100_000)
        shares = allocate(total, weights)
        total_weight = sum(weights.values())

        assert sum(shares.values()) == total
        for key, weight in weights.items():
            exact = total * weight / total_weight
            assert int(exact) <= shares[key] <= int(exact) + 1
        assert allocate(total, dict(reversed(list(weights.items())))) == shares


@pytest.mark.parametrize(
    ("total", "weights"),
    [(-1, {1: 1}), (100, {}), (100, {1: 0}), (100, {1: -1, 2: 2})],
    ids=["negative-total", "empty", "all-zero", "negative-weight"],
)
def test_allocate_rejects_bad_input(total: int, weights: dict[int, int]) -> None:
    with pytest.raises(ValueError):
        allocate(total, weights)


@pytest.mark.parametrize(
    ("cents", "text"),
    [(1480, "$14.80"), (5, "$0.05"), (0, "$0.00"), (-370, "-$3.70"), (123456, "$1,234.56")],
)
def test_fmt_sgd(cents: int, text: str) -> None:
    assert fmt_sgd(cents) == text


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("14.80", 1480),
        ("14.8", 1480),
        ("14", 1400),
        ("$14.80", 1480),
        ("S$ 7.40", 740),
        ("  0.01 ", 1),
        ("500", 50_000),
    ],
)
def test_parse_amount(text: str, cents: int) -> None:
    assert parse_amount(text) == cents


@pytest.mark.parametrize("text", ["", "abc", "14.801", "-1", "1,000", "0", "0.00", "500.01"])
def test_parse_amount_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        parse_amount(text)


def test_parse_amount_custom_limit() -> None:
    assert parse_amount("1200", max_cents=200_000) == 120_000
