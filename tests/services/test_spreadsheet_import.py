from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from badminton.db.models import (
    Attendance,
    AuditLog,
    Booking,
    Member,
    OpeningBalance,
    Session,
    ShuttleBatch,
    Transfer,
)
from badminton.domain.ledger import EntryKind
from badminton.domain.money import allocate
from badminton.domain.sheet import read_items
from badminton.domain.timeutil import at_sgt
from badminton.services import spreadsheet
from badminton.services.repo import load_ledger
from badminton.services.spreadsheet import (
    ImportRefused,
    TallyMismatch,
    check_can_import,
    cutoff,
    import_sheet,
    match_members,
)

HEADER = ("Date", "Item", "Price", "Paid By", "zy", "shawn", "yt", "Number of Shares")
SUMMARY = read_items(
    HEADER,
    [
        (2, (datetime(2026, 10, 11), "court", 14.8, "zy", 1, 1, None)),
        (3, (datetime(2026, 10, 18), "court", 14.8, "shawn", 1, 1, 1)),
        (4, (datetime(2026, 10, 18), "shuttle", 76.79, "yt", 0, 0, None)),  # unused tube
    ],
)
# zy and shawn: 14.80 − 12.33⅓ = +2.46⅔; yt: 76.79 − 4.93⅓ − the 76.79 tube = −4.93⅓.
OPENING = {"zy": 247, "shawn": 247, "yt": -494}
IMPORTED_AT = at_sgt(date(2026, 10, 23), 9)


async def run_import(db: AsyncSession, chosen: dict[str, int] | None = None):
    matches = await match_members(db, SUMMARY.players, chosen or {})
    result = await import_sheet(db, SUMMARY, matches, now=IMPORTED_AT)
    await db.commit()
    return result


def sunday(day: date) -> datetime:
    return at_sgt(day, 11)


async def add_session(db: AsyncSession, day: date, payer: int, players: list[int]) -> None:
    start = sunday(day)
    session = Session(starts_at=start, ends_at=start + timedelta(hours=2))
    db.add(session)
    await db.flush()
    db.add(
        Booking(
            session_id=session.id,
            payer_member_id=payer,
            submitted_by_member_id=payer,
            starts_at=start,
            minutes=120,
            amount_cents=1480,
        )
    )
    db.add_all(Attendance(session_id=session.id, member_id=p, status="in") for p in players)


async def test_carries_over_each_balance_and_the_unused_tube(db: AsyncSession) -> None:
    zy = Member(display_name="ZY", telegram_user_id=111)
    shawn = Member(display_name="Shawn L", username="shawn", telegram_user_id=222)
    db.add_all([zy, shawn])
    await db.commit()

    result = await run_import(db)

    yt = await db.scalar(select(Member).where(Member.display_name == "yt"))
    assert yt.telegram_user_id is None  # a guest, linked when they join
    assert result.member_ids == {"zy": zy.id, "shawn": shawn.id, "yt": yt.id}
    assert result.guests == ("yt",)
    assert result.opening_cents == OPENING
    rows = await db.scalars(select(OpeningBalance).order_by(OpeningBalance.member_id))
    assert [(row.member_id, row.amount_cents) for row in rows] == [
        (zy.id, 247),
        (shawn.id, 247),
        (yt.id, -494),
    ]
    batch = await db.get(ShuttleBatch, result.batch_id)
    assert (batch.payer_member_id, batch.amount_cents) == (yt.id, 7679)
    assert batch.starts_at == at_sgt(date(2026, 10, 19), 0) == cutoff(SUMMARY)

    ledger = await load_ledger(db, IMPORTED_AT)
    assert ledger.balances == {zy.id: 247, shawn.id: 247, yt.id: -494} == result.balances
    assert ledger.open_batch_id == batch.id
    audit = await db.scalar(select(AuditLog).where(AuditLog.action == "sheet.import"))
    assert audit.payload["after"]["yt"] == {"member_id": yt.id, "opening_cents": -494}
    assert audit.payload["guests"] == ["yt"]


async def test_the_tube_is_billed_by_hours_once_the_next_one_is_bought(db: AsyncSession) -> None:
    result = await run_import(db)
    zy, shawn, yt = (result.member_ids[name] for name in ("zy", "shawn", "yt"))
    await add_session(db, date(2026, 10, 25), zy, [zy, shawn, yt])
    await add_session(db, date(2026, 11, 1), zy, [zy, shawn])
    db.add(
        ShuttleBatch(
            payer_member_id=shawn,
            amount_cents=8000,
            starts_at=at_sgt(date(2026, 11, 2), 0),
            created_at=at_sgt(date(2026, 11, 2), 9),
        )
    )
    await db.commit()

    ledger = await load_ledger(db, at_sgt(date(2026, 11, 3), 9))

    shares = {
        e.member_id: (-e.amount_cents, e.minutes)
        for e in ledger.entries
        if e.kind is EntryKind.SHUTTLE_SHARE and e.batch_id == result.batch_id
    }
    expected = allocate(7679, {zy: 240, shawn: 240, yt: 120})
    assert shares == {
        zy: (expected[zy], 240),
        shawn: (expected[shawn], 240),
        yt: (expected[yt], 120),
    }
    court = allocate(1480, {zy: 1, shawn: 1, yt: 1})
    assert ledger.balance(yt) == -494 + 7679 - expected[yt] - court[yt]
    assert sum(ledger.balances.values()) == 0


async def test_matches_chosen_members_and_refuses_unclear_ones(db: AsyncSession) -> None:
    first = Member(display_name="yt", telegram_user_id=1)
    second = Member(display_name="Yu Ting", username="yt", telegram_user_id=2)
    db.add_all([first, second])
    await db.commit()

    with pytest.raises(ImportRefused, match=r"'yt' could be member #1, #2"):
        await match_members(db, SUMMARY.players, {})
    with pytest.raises(ImportRefused, match=r"There's no member #99 \(for 'zy'\)"):
        await match_members(db, SUMMARY.players, {"zy": 99})
    with pytest.raises(ImportRefused, match=r"No player column called 'nic'"):
        await match_members(db, SUMMARY.players, {"nic": first.id})
    with pytest.raises(ImportRefused, match=r"'zy' and 'yt' are both member #2"):
        await match_members(db, SUMMARY.players, {"zy": second.id, "yt": second.id})

    matches = await match_members(db, SUMMARY.players, {"yt": second.id})
    assert [(m.name, m.member and m.member.id, m.how) for m in matches] == [
        ("zy", None, "new guest"),
        ("shawn", None, "new guest"),
        ("yt", second.id, "chosen with --member"),
    ]


async def test_refuses_to_import_twice(db: AsyncSession) -> None:
    await run_import(db)

    with pytest.raises(ImportRefused, match="already been carried over"):
        await run_import(db)


async def test_refuses_when_a_shuttle_batch_is_already_recorded(db: AsyncSession) -> None:
    zy = Member(display_name="zy", telegram_user_id=1)
    db.add(zy)
    await db.flush()
    db.add(ShuttleBatch(payer_member_id=zy.id, amount_cents=7679, starts_at=IMPORTED_AT))
    await db.commit()

    with pytest.raises(ImportRefused, match="unused tube must become the first batch"):
        await run_import(db)
    assert await db.scalar(select(OpeningBalance.member_id)) is None


async def test_refuses_a_batch_or_payment_from_the_time_the_sheet_covers(
    db: AsyncSession,
) -> None:
    no_tube = read_items(HEADER, [(2, (datetime(2026, 10, 18), "court", 14.8, "zy", 1, 1, 1))])
    zy = Member(display_name="zy", telegram_user_id=1)
    yt = Member(display_name="yt", telegram_user_id=2)
    db.add_all([zy, yt])
    await db.flush()
    later = ShuttleBatch(payer_member_id=zy.id, amount_cents=8000, starts_at=cutoff(no_tube))
    db.add(later)
    await db.commit()
    # A batch from the bot's time is fine when the sheet has no unused tube.
    await check_can_import(db, no_tube)

    earlier = ShuttleBatch(
        payer_member_id=zy.id, amount_cents=7679, starts_at=at_sgt(date(2026, 10, 18), 13)
    )
    db.add(earlier)
    await db.commit()
    with pytest.raises(ImportRefused, match="shuttle batch from Sun 18 Oct, which the sheet"):
        await check_can_import(db, no_tube)

    await db.delete(earlier)
    db.add(
        Transfer(
            from_member_id=yt.id,
            to_member_id=zy.id,
            amount_cents=800,
            created_at=at_sgt(date(2026, 10, 18), 20),
        )
    )
    await db.commit()
    with pytest.raises(ImportRefused, match=r"payment from Sun 18 Oct, .* new, empty database"):
        await check_can_import(db, no_tube)


async def test_names_match_whatever_their_case(db: AsyncSession) -> None:
    strauss = Member(display_name="Strauss", telegram_user_id=1)
    db.add(strauss)
    await db.commit()

    [match] = await match_members(db, ("STRAUß",), {})

    assert (match.member, match.how) == (strauss, "same name")


async def test_refuses_when_the_bot_has_a_session_the_sheet_covers(db: AsyncSession) -> None:
    zy = Member(display_name="zy", telegram_user_id=1)
    db.add(zy)
    await db.flush()
    await add_session(db, date(2026, 10, 18), zy.id, [zy.id])
    await db.commit()

    with pytest.raises(ImportRefused, match="already has a session on Sun 18 Oct"):
        await run_import(db)


async def test_nothing_is_kept_if_the_balances_dont_tally(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = spreadsheet.get_balances
    calls = 0

    async def off_by_a_cent(session: AsyncSession, as_of: datetime) -> dict[int, int]:
        nonlocal calls
        calls += 1
        balances = await real(session, as_of)
        if calls == 2:  # the check after importing
            return {member: cents + 1 for member, cents in balances.items()}
        return balances

    monkeypatch.setattr(spreadsheet, "get_balances", off_by_a_cent)
    matches = await match_members(db, SUMMARY.players, {})

    with pytest.raises(TallyMismatch, match="Balances don't tally for zy, shawn, yt"):
        await import_sheet(db, SUMMARY, matches, now=IMPORTED_AT)
    await db.rollback()
    assert await db.scalar(select(Member.id)) is None
