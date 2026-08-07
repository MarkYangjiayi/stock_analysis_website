from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.trading_calendar import latest_completed_us_session
from models import DailyPrice, FinancialStatement, SecurityMaster, Ticker
from core.time_utils import utc_now


@dataclass(frozen=True)
class FreshnessAssessment:
    exists: bool
    needs_sync: bool
    latest_price_date: Optional[date]
    expected_price_date: date
    profile_updated_at: Optional[datetime]
    has_quarterly_fundamentals: bool
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


async def assess_ticker_freshness(
    db: AsyncSession,
    ticker: str,
    reference_date: Optional[date] = None,
) -> FreshnessAssessment:
    today = reference_date or date.today()
    expected_price_date = latest_completed_us_session(today)
    ticker_obj = await db.get(Ticker, ticker)
    if ticker_obj is None:
        return FreshnessAssessment(False, True, None, expected_price_date, None, False, "ticker_missing")
    price_result = await db.execute(
        select(func.max(DailyPrice.date)).where(DailyPrice.ticker == ticker)
    )
    latest_price = price_result.scalar_one_or_none()
    quarterly_result = await db.execute(
        select(func.count(FinancialStatement.id)).where(
            FinancialStatement.ticker == ticker,
            FinancialStatement.period == "Quarterly",
        )
    )
    has_quarterly = quarterly_result.scalar_one() > 0
    security_result = await db.execute(
        select(SecurityMaster.asset_type).where(SecurityMaster.canonical_ticker == ticker)
    )
    asset_type = (security_result.scalar_one_or_none() or "").upper()
    expects_fundamentals = not any(label in asset_type for label in ("ETF", "FUND", "INDEX"))
    reference_datetime = (
        datetime.combine(today, time.max)
        if reference_date is not None
        else utc_now()
    )
    profile_cutoff = reference_datetime - timedelta(days=settings.PROFILE_MAX_STALENESS_DAYS)

    if latest_price is None:
        reason = "price_missing"
    elif latest_price < expected_price_date:
        reason = "price_stale"
    elif ticker_obj.last_updated is None or ticker_obj.last_updated < profile_cutoff:
        reason = "profile_stale"
    elif expects_fundamentals and (
        not str(ticker_obj.description or "").strip()
        or not str(ticker_obj.exchange or "").strip()
    ):
        reason = "profile_incomplete"
    elif expects_fundamentals and not has_quarterly:
        reason = "fundamentals_missing"
    else:
        reason = "fresh"
    return FreshnessAssessment(
        True,
        reason != "fresh",
        latest_price,
        expected_price_date,
        ticker_obj.last_updated,
        has_quarterly,
        reason,
    )
