"""Owner-only Today view for the home page.

The market regime, theme clusters and movers come from the most recent daily
report's saved evidence, so the page shows exactly what the Feishu push showed.
The report only carries the core assets, the first watchlist names and the
next session's events, so watchlist tags (same rules as the report tags) and a
seven-day calendar are computed on request and cached for a few minutes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select

from core.config import settings
from core.trading_calendar import latest_completed_us_session
from database import async_session_maker
from models import DailyReportRun
from services.report_market import NY, Instrument, collect_events
from services.watchlist_signals import expectation_signals, price_signals, signal_as_of, signal_tags

logger = logging.getLogger(__name__)
REPORT_TYPES = ("morning_briefing", "post_market_summary")
# Older runs may predate the regime line; look a few runs back for one with it.
EVIDENCE_LOOKBACK_RUNS = 6
EVENT_WINDOW_DAYS = 7
CACHE_SECONDS = 15 * 60
SIGNALS_TIMEOUT_SECONDS = 30
EVENTS_TIMEOUT_SECONDS = 25

_cache: dict[tuple, tuple[float, dict[str, Any]]] = {}
_cache_lock = asyncio.Lock()


def _mover(row: dict[str, Any]) -> dict[str, Any]:
    news = next((item for item in row.get("news") or [] if isinstance(item, dict) and item.get("title")), None)
    return {
        "ticker": row.get("ticker"),
        "company_name": row.get("company_name"),
        "price_change": row.get("price_change"),
        "quote_timestamp": row.get("quote_timestamp"),
        # Headlines are leads, not verified causes; the page labels them so.
        "news_title": news.get("title") if news else None,
        "news_link": news.get("link") if news else None,
    }


async def latest_report_evidence() -> Optional[dict[str, Any]]:
    """The newest daily report's clusters and movers plus the newest regime line; None before any report."""
    async with async_session_maker() as db:
        runs = (await db.execute(
            select(DailyReportRun)
            .where(DailyReportRun.report_type.in_(REPORT_TYPES), DailyReportRun.market_context.is_not(None))
            .order_by(DailyReportRun.id.desc())
            .limit(EVIDENCE_LOOKBACK_RUNS)
        )).scalars().all()
    if not runs:
        return None
    run, context = runs[0], runs[0].market_context or {}
    # If the newest report could not build the regime line, show the previous one with its own time.
    regime_run = next((item for item in runs if (item.market_context or {}).get("regime")), None)
    return {
        "run_id": run.id,
        "report_type": run.report_type,
        "report_date": context.get("report_date"),
        "captured_at": context.get("captured_at"),
        "status": run.status,
        "delivered": bool(run.notification_delivered),
        "regime": regime_run.market_context["regime"] if regime_run else None,
        "regime_captured_at": regime_run.market_context.get("captured_at") if regime_run else None,
        "anomaly_clusters": context.get("anomaly_clusters"),
        "movers": [_mover(row) for row in run.source_results or [] if isinstance(row, dict)],
        "breadth": context.get("breadth"),
    }


def _core_symbols() -> list[str]:
    return [symbol.strip().upper() for symbol in settings.DAILY_REPORT_CORE_SYMBOLS.split(",") if symbol.strip()]


async def _watchlist_tags(tickers: list[str], now: datetime) -> dict[str, Any]:
    today = now.astimezone(NY).date()
    signals = await price_signals(tickers, latest_completed_us_session(today))
    signals["expectations"] = await expectation_signals(tickers, today)
    return {
        "target": signals.get("target"),
        "items": [
            {"ticker": ticker, "tags": signal_tags(ticker, signals), "as_of": signal_as_of(ticker, signals)}
            for ticker in tickers
        ],
    }


async def _upcoming_events(tickers: list[str], now: datetime) -> dict[str, Any]:
    start = now.astimezone(NY).date()
    events = await collect_events(
        [Instrument(ticker, ticker, "core") for ticker in tickers],
        now,
        start=start,
        end=start + timedelta(days=EVENT_WINDOW_DAYS - 1),
    )
    return {**events, "window_days": EVENT_WINDOW_DAYS}


async def _bounded(label: str, operation, timeout: float, warnings: list[str]) -> Optional[dict[str, Any]]:
    try:
        return await asyncio.wait_for(operation, timeout)
    except Exception as exc:
        logger.warning("Today %s unavailable (%s)", label, type(exc).__name__)
        warnings.append(label)
        return None


async def _personal_sections(watchlist: list[str], now: datetime) -> dict[str, Any]:
    event_symbols = list(dict.fromkeys([*_core_symbols(), *watchlist]))
    key = (tuple(watchlist), tuple(event_symbols), now.astimezone(NY).date().isoformat())
    async with _cache_lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
            return cached[1]
        warnings: list[str] = []
        tags, events = await asyncio.gather(
            _bounded("自选股标签", _watchlist_tags(watchlist, now), SIGNALS_TIMEOUT_SECONDS, warnings),
            _bounded("未来 7 天日历", _upcoming_events(event_symbols, now), EVENTS_TIMEOUT_SECONDS, warnings),
        )
        sections = {"watchlist": tags, "events": events, "unavailable": warnings}
        # Cache only complete results so a provider hiccup is retried on the next load.
        _cache.clear()
        if not warnings:
            _cache[key] = (time.monotonic(), sections)
        return sections


async def build_today(watchlist: list[str], *, now: Optional[datetime] = None) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    report, sections = await asyncio.gather(latest_report_evidence(), _personal_sections(watchlist, current))
    return {
        "generated_at": current.isoformat(),
        "report": report,
        "watchlist": sections["watchlist"],
        "events": sections["events"],
        "unavailable": sections["unavailable"],
    }
