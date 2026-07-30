import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.trading_calendar import latest_completed_us_session
from models import StockScreenerSnapshot
from services.ai_assistant import (
    AttributionGenerationError,
    generate_anomaly_attribution,
)
from services.eodhd_client import get_bulk_realtime_prices
from services.news_fetcher import NewsFetchError, fetch_yahoo_news


logger = logging.getLogger(__name__)
_NEW_YORK = ZoneInfo("America/New_York")


class AnomalyScanError(RuntimeError):
    """A scan-level failure that can be safely surfaced to API clients."""


class AnomalyDataUnavailable(AnomalyScanError):
    """Raised when the market-data input is missing, invalid, or stale."""


@dataclass(frozen=True)
class AnomalyScanData:
    universe_as_of: date
    quote_as_of: datetime
    results: List[Dict[str, Any]]


def _quote_timestamp(raw_value: Any) -> Optional[datetime]:
    if raw_value is None:
        return None
    try:
        if isinstance(raw_value, (int, float)) or str(raw_value).replace(".", "", 1).isdigit():
            numeric = float(raw_value)
            if numeric > 10_000_000_000:
                numeric /= 1_000
            return datetime.fromtimestamp(numeric, tz=timezone.utc)
        parsed = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (OSError, OverflowError, TypeError, ValueError):
        return None


def _canonical_quote_code(raw_code: Any) -> str:
    code = str(raw_code or "").strip().upper()
    if code and not code.endswith(".US"):
        code = f"{code}.US"
    return code


def _news_summary(item: Dict[str, Any]) -> str:
    return (
        f"Title: {item.get('title', '')}\n"
        f"Publisher: {item.get('publisher', '')}\n"
        f"Published: {item.get('pub_date', '')}\n"
        f"Summary: {item.get('summary', '')}"
    )


async def _analyze_candidate(
    candidate: Dict[str, Any],
    *,
    semaphore: asyncio.Semaphore,
    news_client: httpx.AsyncClient,
) -> Dict[str, Any]:
    ticker = candidate["ticker"]
    base_result = {
        "ticker": ticker,
        "company_name": candidate["company_name"],
        "date": candidate["quote_timestamp"].astimezone(_NEW_YORK).date().isoformat(),
        "quote_timestamp": candidate["quote_timestamp"].isoformat().replace("+00:00", "Z"),
        "price_change": round(candidate["price_change"], 2),
    }

    async with semaphore:
        sources: List[Dict[str, Any]] = []

        async def generate_result() -> Dict[str, Any]:
            nonlocal sources
            news_items = await fetch_yahoo_news(
                ticker,
                lookback_hours=settings.ANOMALY_NEWS_LOOKBACK_HOURS,
                client=news_client,
            )
            sources = news_items[:3]
            if not sources:
                return {
                    **base_result,
                    "ai_analysis": "缺乏明确新闻催化剂，可能为资金面或技术面行为。",
                    "attribution_status": "no_news",
                    "news": [],
                    "top_news_links": [],
                }

            analysis = await generate_anomaly_attribution(
                ticker=ticker,
                price_change=base_result["price_change"],
                news_list=[_news_summary(item) for item in sources],
            )
            return {
                **base_result,
                "ai_analysis": analysis,
                "attribution_status": "completed",
                "news": sources,
                "top_news_links": [item["link"] for item in sources],
            }

        try:
            return await asyncio.wait_for(
                generate_result(),
                timeout=settings.ANOMALY_ATTRIBUTION_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning("Attribution timed out for %s", ticker)
            return {
                **base_result,
                "ai_analysis": "归因服务超时，本次仅展示行情异动。",
                "attribution_status": "timed_out",
                "news": sources,
                "top_news_links": [item["link"] for item in sources],
            }
        except NewsFetchError:
            logger.warning("News unavailable while attributing %s", ticker)
            return {
                **base_result,
                "ai_analysis": "新闻源暂时不可用，本次仅展示行情异动。",
                "attribution_status": "news_unavailable",
                "news": [],
                "top_news_links": [],
            }
        except AttributionGenerationError:
            logger.warning("AI attribution unavailable for %s", ticker)
            return {
                **base_result,
                "ai_analysis": "AI 归因暂时不可用，请结合下方新闻来源判断。",
                "attribution_status": "attribution_unavailable",
                "news": sources,
                "top_news_links": [item["link"] for item in sources],
            }


async def scan_and_analyze_anomalies(
    db: AsyncSession,
    limit_count: int = 5,
    *,
    threshold_pct: Optional[float] = None,
) -> AnomalyScanData:
    """Scan current US quotes and attribute the largest moves with bounded fan-out."""
    limit_count = max(1, min(int(limit_count), 10))
    threshold = (
        settings.ANOMALY_MOVE_THRESHOLD_PCT
        if threshold_pct is None
        else max(0.0, float(threshold_pct))
    )

    max_date_result = await db.execute(select(func.max(StockScreenerSnapshot.date)))
    universe_as_of = max_date_result.scalar()
    if not universe_as_of:
        raise AnomalyDataUnavailable("No published screener universe is available")

    universe_result = await db.execute(
        select(StockScreenerSnapshot.ticker, StockScreenerSnapshot.name)
        .where(StockScreenerSnapshot.date == universe_as_of)
        .order_by(StockScreenerSnapshot.market_cap.desc().nullslast())
        .limit(500)
    )
    universe = universe_result.all()
    await db.rollback()
    if not universe:
        raise AnomalyDataUnavailable("The published screener universe is empty")

    ticker_to_name = {
        row.ticker.upper(): (row.name or row.ticker)
        for row in universe
    }
    logger.info("Anomaly scan: target universe length = %s", len(ticker_to_name))

    all_realtime_data = await get_bulk_realtime_prices("US")
    logger.info(
        "Anomaly scan: fetched %s realtime quotes",
        len(all_realtime_data) if all_realtime_data else 0,
    )
    if not all_realtime_data:
        raise AnomalyDataUnavailable("Real-time market data is unavailable")

    now_utc = datetime.now(timezone.utc)
    minimum_quote_date = latest_completed_us_session(
        now_utc.astimezone(_NEW_YORK).date()
    )
    matching_quote_times: List[datetime] = []
    candidates: List[Dict[str, Any]] = []

    for quote in all_realtime_data:
        code = _canonical_quote_code(quote.get("code"))
        if not code or code not in ticker_to_name:
            continue

        quote_time = _quote_timestamp(quote.get("timestamp"))
        if quote_time is None or quote_time > now_utc + timedelta(minutes=5):
            continue
        matching_quote_times.append(quote_time)
        if quote_time.astimezone(_NEW_YORK).date() < minimum_quote_date:
            continue

        try:
            change_pct = float(quote.get("change_p"))
        except (TypeError, ValueError):
            continue
        if abs(change_pct) < threshold:
            continue
        candidates.append({
            "ticker": code,
            "company_name": ticker_to_name[code],
            "price_change": change_pct,
            "quote_timestamp": quote_time,
        })

    if not matching_quote_times:
        raise AnomalyDataUnavailable("Real-time market data has no valid timestamps")
    quote_as_of = max(matching_quote_times)
    if quote_as_of.astimezone(_NEW_YORK).date() < minimum_quote_date:
        raise AnomalyDataUnavailable("Real-time market data is stale")

    candidates.sort(key=lambda item: abs(item["price_change"]), reverse=True)
    selected = candidates[:limit_count]
    if not selected:
        return AnomalyScanData(
            universe_as_of=universe_as_of,
            quote_as_of=quote_as_of,
            results=[],
        )

    logger.info(
        "Anomaly scan: attributing %s candidates with threshold %.2f%%",
        len(selected),
        threshold,
    )
    semaphore = asyncio.Semaphore(
        max(1, settings.ANOMALY_ATTRIBUTION_CONCURRENCY)
    )
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as news_client:
        results = await asyncio.gather(*[
            _analyze_candidate(
                candidate,
                semaphore=semaphore,
                news_client=news_client,
            )
            for candidate in selected
        ])

    return AnomalyScanData(
        universe_as_of=universe_as_of,
        quote_as_of=quote_as_of,
        results=results,
    )
