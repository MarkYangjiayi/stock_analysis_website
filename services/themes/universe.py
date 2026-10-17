"""The company universe theme jobs score: the latest published screener cross-section."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import DataPublication, PipelineRun, StockScreenerSnapshot, Ticker
from services.screener_query import _live_index_membership_condition, _primary_listing_condition


@dataclass(frozen=True)
class CompanyProfile:
    ticker: str
    name: Optional[str]
    sector: Optional[str]
    industry: Optional[str]
    description: Optional[str]
    market_cap: Optional[float]


async def latest_screener_publication_on_or_before(db: AsyncSession, as_of: date) -> Optional[date]:
    """Latest published screener snapshot date that is not after ``as_of``."""
    result = await db.execute(
        select(DataPublication.as_of_date)
        .join(PipelineRun, PipelineRun.id == DataPublication.pipeline_run_id)
        .where(
            DataPublication.dataset == "screener",
            DataPublication.status == "published",
            PipelineRun.status == "published",
            DataPublication.as_of_date <= as_of,
        )
        .order_by(DataPublication.as_of_date.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def load_theme_universe(db: AsyncSession, as_of: date) -> tuple[Optional[date], dict[str, CompanyProfile]]:
    """Primary-listed live R3000+NDX members of the latest published screener snapshot <= as_of.

    Returns (snapshot_date, {ticker: profile}); (None, {}) when nothing is published.
    Descriptions come from ``tickers.description`` (EODHD General.Description) and are
    current text only, so description-based mappings are not point-in-time.
    """
    snapshot_date = await latest_screener_publication_on_or_before(db, as_of)
    if snapshot_date is None:
        return None, {}
    rows = await db.execute(
        select(
            StockScreenerSnapshot.ticker,
            StockScreenerSnapshot.name,
            StockScreenerSnapshot.sector,
            StockScreenerSnapshot.industry,
            StockScreenerSnapshot.market_cap,
            Ticker.description,
            Ticker.name,
        )
        .outerjoin(Ticker, Ticker.ticker == StockScreenerSnapshot.ticker)
        .where(
            StockScreenerSnapshot.date == snapshot_date,
            _primary_listing_condition(),
            _live_index_membership_condition(snapshot_date),
        )
    )
    universe: dict[str, CompanyProfile] = {}
    for ticker, name, sector, industry, market_cap, description, ticker_name in rows.all():
        universe[ticker] = CompanyProfile(
            ticker=ticker,
            name=name or ticker_name,
            sector=sector,
            industry=industry,
            description=description,
            market_cap=float(market_cap) if market_cap is not None else None,
        )
    return snapshot_date, universe
