from datetime import date, timedelta
from typing import Iterable, List, Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import UniverseMembership


async def record_universe_membership(
    db: AsyncSession,
    universe: str,
    tickers: Iterable[str],
    effective_date: date,
    source_run_id: Optional[int] = None,
) -> None:
    current = {ticker.upper() for ticker in tickers}
    result = await db.execute(
        select(UniverseMembership).where(
            UniverseMembership.universe == universe,
            UniverseMembership.effective_to.is_(None),
        )
    )
    active = {row.ticker: row for row in result.scalars().all()}
    exited = set(active) - current
    if exited:
        await db.execute(
            update(UniverseMembership)
            .where(
                UniverseMembership.universe == universe,
                UniverseMembership.ticker.in_(exited),
                UniverseMembership.effective_to.is_(None),
            )
            .values(effective_to=effective_date - timedelta(days=1))
        )
    new_rows = [
        {
            "universe": universe,
            "ticker": ticker,
            "effective_from": effective_date,
            "source": "EODHD",
            "source_run_id": source_run_id,
        }
        for ticker in current - set(active)
    ]
    if new_rows:
        await db.execute(
            insert(UniverseMembership).values(new_rows).on_conflict_do_nothing(
                index_elements=["universe", "ticker", "effective_from"]
            )
        )


async def universe_as_of(db: AsyncSession, universe: str, as_of_date: date) -> List[str]:
    result = await db.execute(
        select(UniverseMembership.ticker).where(
            UniverseMembership.universe == universe,
            UniverseMembership.effective_from <= as_of_date,
            (UniverseMembership.effective_to.is_(None) | (UniverseMembership.effective_to >= as_of_date)),
        )
    )
    return list(result.scalars().all())
