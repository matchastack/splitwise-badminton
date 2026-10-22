"""The monthly run (FR-INV-1): generate the invoice, post it, carry over the previous
one's unpaid lines and DM everyone their statement."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot

from badminton.config import Settings
from badminton.db.models import Invoice
from badminton.domain.timeutil import fmt_period, previous_period, sgt_date
from badminton.services.invoice import (
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
    """Returns None if ``period`` was already invoiced."""
    previous = await previous_invoice(db)
    invoice = await generate_invoice(db, period=period, now=now)
    if invoice is None:
        return None
    await db.commit()
    await publish_invoice(bot, db, settings, invoice)
    await db.commit()
    if previous is not None:
        await refresh_invoice_card(bot, db, previous, carried_into=fmt_period(period))
    await send_statements(bot, db, invoice)
    await db.commit()
    return invoice


async def monthly_invoice(context: TickContext) -> None:
    """Tick step: on the 1st (SGT), or a few days after if runs were missed, invoice the
    previous month unless it already has an invoice (FR-INV-1)."""
    if sgt_date(context.now).day > CATCH_UP_DAYS:
        return
    async with context.sessionmaker() as db:
        await run_invoice(
            context.bot,
            db,
            context.settings,
            period=previous_period(context.now),
            now=context.now,
        )
