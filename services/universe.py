from datetime import date, timedelta
from typing import Iterable, List, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import UniverseMembership


async def record_universe_membership(
    db: AsyncSession,
    universe: str,
    tickers: Iterable[str],
    effective_date: date,
    source_run_id: Optional[int] = None,
    minimum_retained_fraction: Optional[float] = None,
    known_exits: Optional[Iterable[str]] = None,
) -> None:
    current = {ticker.upper() for ticker in tickers}
    allowed_exits = (
        {ticker.upper() for ticker in known_exits} - current
        if known_exits is not None
        else set()
    )
    result = await db.execute(
        select(UniverseMembership).where(
            UniverseMembership.universe == universe,
            UniverseMembership.effective_to.is_(None),
        )
    )
    active = {row.ticker: row for row in result.scalars().all()}
    if minimum_retained_fraction is not None:
        if not 0 < minimum_retained_fraction <= 1:
            raise ValueError("minimum_retained_fraction must be in (0, 1]")
        if active:
            protected_active = set(active) - allowed_exits
            retained = (
                len(protected_active & current) / len(protected_active)
                if protected_active
                else 1.0
            )
            if retained < minimum_retained_fraction:
                raise ValueError(
                    f"Observed {universe} universe retained only {retained:.2%} of "
                    f"{len(protected_active)} active constituents after known exits; "
                    "refusing to close memberships"
                )
    exited = set(active) - current
    if exited:
        same_day_exits = {
            ticker
            for ticker in exited
            if active[ticker].effective_from == effective_date
        }
        if same_day_exits:
            await db.execute(
                delete(UniverseMembership).where(
                    UniverseMembership.universe == universe,
                    UniverseMembership.ticker.in_(same_day_exits),
                    UniverseMembership.effective_from == effective_date,
                )
            )
        earlier_exits = exited - same_day_exits
        if earlier_exits:
            await db.execute(
                update(UniverseMembership)
                .where(
                    UniverseMembership.universe == universe,
                    UniverseMembership.ticker.in_(earlier_exits),
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
