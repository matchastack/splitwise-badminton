"""The monthly run (FR-INV-1): generate the invoice, post it, carry over the previous
one's unpaid lines and DM everyone their statement."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot

from badminton.config import Settings
from badminton.db.models import Invoice
from badminton.domain.timeutil import fmt_period, period_of, previous_period, sgt_date
from badminton.services.invoice import (
    discard_invoice,
    generate_invoice,
    previous_invoice,
    publish_invoice,
    refresh_invoice_card,
)
from badminton.services.statements import send_statements
from badminton.services.tick import TickContext

# How many days into a month a missed invoice run is still caught up. Beyond that (e.g. a
# first deployment mid-month) the previous month is left alone.
CATCH_UP_DAYS = 7


async def run_invoice(
    bot: Bot, db: AsyncSession, settings: Settings, *, period: str, now: datetime
) -> Invoice | None:
    """Returns None if ``period`` was already invoiced. If the card can't be posted, the
    invoice is discarded so the next run tries again."""
    previous = await previous_invoice(db)
    previous_id = previous.id if previous is not None else None
    invoice = await generate_invoice(db, period=period, now=now)
    if invoice is None:
        return None
    invoice_id = invoice.id
    await db.commit()
    try:
        await publish_invoice(bot, db, settings, invoice)
    except Exception:
        await db.rollback()
        await discard_invoice(db, invoice_id, previous_id)
        await db.commit()
        raise
    await db.commit()
    if previous is not None:
        await refresh_invoice_card(bot, db, previous, carried_into=fmt_period(period))
    await send_statements(bot, db, invoice)
    await db.commit()
    return invoice


def scheduled_period(now: datetime) -> str | None:
    """The period the monthly run invoices at ``now``: the previous month, on the 1st (SGT)
    or a few days after if runs were missed. None later in the month."""
    if sgt_date(now).day > CATCH_UP_DAYS:
        return None
    return previous_period(now)


async def manual_period(db: AsyncSession, now: datetime) -> str:
    """The period /invoice fills: the previous month while the monthly run would still
    invoice it, so the two can't both post one; otherwise the current month."""
    scheduled = scheduled_period(now)
    if scheduled is not None:
        existing = await db.scalar(select(Invoice.id).where(Invoice.period == scheduled))
        if existing is None:
            return scheduled
    return period_of(now)


async def monthly_invoice(context: TickContext) -> None:
    """Tick step: invoice the scheduled period unless it already has an invoice (FR-INV-1)."""
    period = scheduled_period(context.now)
    if period is None:
        return
    async with context.sessionmaker() as db:
        await run_invoice(context.bot, db, context.settings, period=period, now=context.now)
