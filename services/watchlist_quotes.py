"""Delayed watchlist quotes with short daily-close sparklines.

EODHD bills the real-time endpoint per symbol, so quotes are cached per ticker:
about a minute while the market is open (and briefly after the close, while the
15-minute delayed feed settles), and much longer otherwise. Sparklines prefer
the local ``daily_prices`` table and fall back to one EOD request per ticker per
New York day. A global symbol budget caps provider usage; when it is exhausted
the response degrades to cached or missing data instead of failing.
"""

import asyncio
import logging
import math
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.trading_calendar import NEW_YORK, UsMarketStatus, us_market_status
from models import DailyPrice
from services import eodhd_client


logger = logging.getLogger(__name__)

MAX_TICKERS = 20
SPARKLINE_SESSIONS = 30
DELAY_MINUTES = 15
LIVE_TTL = timedelta(seconds=60)
IDLE_TTL = timedelta(minutes=30)
CLOSE_SETTLE = timedelta(minutes=DELAY_MINUTES + 15)
HISTORY_LOOKBACK = timedelta(days=60)
# Local history older than this is treated as missing for sparkline purposes.
HISTORY_MAX_GAP = timedelta(days=7)

_quote_cache: dict[str, tuple[datetime, Optional[dict[str, Any]]]] = {}
_history_cache: dict[str, tuple[date, list[dict[str, Any]]]] = {}
_provider_calls: deque[float] = deque()
_fetch_lock = asyncio.Lock()


def reset_caches() -> None:
    _quote_cache.clear()
    _history_cache.clear()
    _provider_calls.clear()


def _take_provider_budget(symbols: int) -> int:
    """Reserve up to ``symbols`` provider calls from the rolling one-minute budget."""
    now = time.monotonic()
    while _provider_calls and _provider_calls[0] <= now - 60:
        _provider_calls.popleft()
    granted = max(0, min(symbols, settings.WATCHLIST_PROVIDER_SYMBOLS_PER_MINUTE - len(_provider_calls)))
    _provider_calls.extend([now] * granted)
    return granted


def quote_ttl(status: UsMarketStatus, now: datetime) -> timedelta:
    if status["phase"] == "open":
        return LIVE_TTL
    closes_at = status["closes_at"]
    if closes_at is not None and closes_at <= now < closes_at + CLOSE_SETTLE:
        return LIVE_TTL
    return IDLE_TTL


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_quote(ticker: str, raw: dict[str, Any]) -> Optional[dict[str, Any]]:
    if str(raw.get("code", "")).upper() != ticker:
        return None
    price = _number(raw.get("close"))
    timestamp = _number(raw.get("timestamp"))
    if price is None or price <= 0 or timestamp is None:
        return None
    try:
        quoted_at = datetime.fromtimestamp(timestamp, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    previous_close = _number(raw.get("previousClose"))
    if previous_close is not None and previous_close <= 0:
        previous_close = None
    change = price - previous_close if previous_close is not None else None
    return {
        "price": price,
        "previous_close": previous_close,
        "change": change,
        "change_pct": change / previous_close if change is not None and previous_close else None,
        "as_of": quoted_at,
        "session_date": quoted_at.astimezone(NEW_YORK).date(),
    }


async def _refresh_quotes(tickers: list[str], now: datetime, ttl: timedelta) -> None:
    due = [ticker for ticker in tickers if ticker not in _quote_cache or now - _quote_cache[ticker][0] >= ttl]
    granted = _take_provider_budget(len(due))
    if granted < len(due):
        logger.warning("Watchlist quote budget exhausted; serving %s symbols from cache", len(due) - granted)
    due = due[:granted]
    if not due:
        return
    try:
        rows = await eodhd_client.get_live_quotes(due)
    except Exception as exc:  # Provider failures keep the previous snapshot.
        logger.warning("Watchlist quotes unavailable (%s)", type(exc).__name__)
        return
    by_code = {str(row.get("code", "")).upper(): row for row in rows}
    for ticker in due:
        raw = by_code.get(ticker)
        quote = normalize_quote(ticker, raw) if raw else None
        if quote is None and ticker in _quote_cache and _quote_cache[ticker][1] is not None:
            # Keep the last good quote rather than blanking the row on a partial response.
            quote = _quote_cache[ticker][1]
        _quote_cache[ticker] = (now, quote)


async def _local_history(db: AsyncSession, tickers: list[str], now: datetime) -> dict[str, list[dict[str, Any]]]:
    rows = (await db.execute(
        select(DailyPrice.ticker, DailyPrice.date, DailyPrice.adjusted_close, DailyPrice.close)
        .where(DailyPrice.ticker.in_(tickers), DailyPrice.date >= (now - HISTORY_LOOKBACK).date())
        .order_by(DailyPrice.ticker, DailyPrice.date)
    )).all()
    history: dict[str, list[dict[str, Any]]] = {ticker: [] for ticker in tickers}
    for ticker, session_date, adjusted_close, close in rows:
        value = _number(adjusted_close if adjusted_close is not None else close)
        if value is not None and value > 0:
            history[ticker].append({"date": session_date, "close": value})
    return history


def _normalize_eod(rows: Any) -> list[dict[str, Any]]:
    points = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        value = _number(row.get("adjusted_close", row.get("close")))
        try:
            session_date = date.fromisoformat(str(row.get("date")))
        except ValueError:
            continue
        if value is not None and value > 0:
            points.append({"date": session_date, "close": value})
    return sorted(points, key=lambda point: point["date"])


async def _histories(db: AsyncSession, tickers: list[str], now: datetime) -> dict[str, list[dict[str, Any]]]:
    history = await _local_history(db, tickers, now)
    today = now.astimezone(NEW_YORK).date()
    missing = []
    for ticker in tickers:
        points = history[ticker]
        complete = len(points) >= SPARKLINE_SESSIONS // 2 and today - points[-1]["date"] <= HISTORY_MAX_GAP
        if complete:
            continue
        cached = _history_cache.get(ticker)
        if cached and cached[0] == today:
            history[ticker] = cached[1] or points
        else:
            missing.append(ticker)

    granted = _take_provider_budget(len(missing))
    if not granted:
        return history
    async with eodhd_client.create_http_client() as client:
        async def fetch(ticker: str) -> None:
            try:
                rows = await eodhd_client.get_eod_historical_data(
                    ticker, (now - HISTORY_LOOKBACK).date().isoformat(), today.isoformat(), client=client,
                )
            except Exception as exc:
                logger.warning("Watchlist history unavailable for %s (%s)", ticker, type(exc).__name__)
                return
            points = _normalize_eod(rows)
            _history_cache[ticker] = (today, points)
            if points:
                history[ticker] = points
        await asyncio.gather(*(fetch(ticker) for ticker in missing[:granted]))
    return history


async def get_watchlist_quotes(
    db: AsyncSession,
    tickers: list[str],
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    if len(tickers) > MAX_TICKERS:
        raise ValueError(f"At most {MAX_TICKERS} tickers can be quoted at once")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    status = us_market_status(now)
    async with _fetch_lock:
        await _refresh_quotes(tickers, now, quote_ttl(status, now))
        history = await _histories(db, tickers, now)

    items = []
    for ticker in tickers:
        quote = _quote_cache.get(ticker, (now, None))[1]
        sparkline = list(history.get(ticker, []))
        if quote and (not sparkline or quote["session_date"] > sparkline[-1]["date"]):
            sparkline.append({"date": quote["session_date"], "close": quote["price"]})
        items.append({
            "ticker": ticker,
            "quote": quote,
            "sparkline": sparkline[-SPARKLINE_SESSIONS:],
        })
    return {"market": status, "delay_minutes": DELAY_MINUTES, "items": items}
