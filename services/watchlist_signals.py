"""Daily-close technical tags for the core assets and watchlist.

Uses the same RSI(14) definition and stored-price refresh as the RSI monitor,
so the tags agree with its alerts. A post-market report may append today's
delayed close, but only when the quote's previous close lines up with the
stored close; a split, distribution or bad tick then falls back to the last
stored session instead of producing a false signal. Only US listings are
tagged because the session walk follows the NYSE calendar.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
import logging
from typing import Any

import pandas as pd
import pandas_ta_classic as ta
from sqlalchemy import select

from core.config import settings
from core.trading_calendar import latest_completed_us_session
from database import async_session_maker
from models import DailyPrice
from services.report_market import number
from services.rsi_monitor import (
    PRICE_LOOKBACK_DAYS,
    RSI_PERIOD,
    _refresh_missing_prices,
    _trailing_session_prices,
)


logger = logging.getLogger(__name__)
HIGH_LOW_SESSIONS = 252
MIN_HIGH_LOW_SESSIONS = 240
NEAR_EXTREME_PCT = 1.0
LIVE_CLOSE_TOLERANCE = 0.005


def _rsi(closes: list[float]) -> float | None:
    if len(closes) <= RSI_PERIOD:
        return None
    series = ta.rsi(pd.Series(closes, dtype="float64"), length=RSI_PERIOD)
    if series is None or series.empty or pd.isna(series.iloc[-1]):
        return None
    return round(float(series.iloc[-1]), 1)


def _lines_up(stored_close: float | None, stored_adjusted: float | None, quote: dict) -> bool:
    previous, price = number(quote.get("previous_close")), number(quote.get("price"))
    if not stored_close or not previous or not price or price <= 0:
        return False
    if stored_adjusted and abs(stored_adjusted / stored_close - 1) > LIVE_CLOSE_TOLERANCE:
        return False
    return abs(stored_close / previous - 1) <= LIVE_CLOSE_TOLERANCE


async def price_signals(
    symbols: list[str],
    target: date,
    *,
    live_quotes: dict[str, dict] | None = None,
    live_date: date | None = None,
    refresh_timeout: float = 40.0,
) -> dict[str, Any]:
    """Return raw RSI / 52-week values per symbol as of ``target`` (or ``live_date``)."""
    symbols = [symbol for symbol in dict.fromkeys(symbols) if symbol.endswith(".US")]
    evidence: dict[str, Any] = {
        "period": RSI_PERIOD, "oversold": settings.RSI_MONITOR_OVERSOLD,
        "overbought": settings.RSI_MONITOR_OVERBOUGHT, "near_extreme_pct": NEAR_EXTREME_PCT,
        "target": target.isoformat(), "items": {}, "unavailable": [],
    }
    if not symbols:
        return evidence
    try:
        failures = await asyncio.wait_for(_refresh_missing_prices(symbols, target), refresh_timeout)
        evidence["refresh_failures"] = sorted(failures)
    except Exception as exc:
        logger.warning("Watchlist signal price refresh incomplete (%s)", type(exc).__name__)
        evidence["refresh_failures"] = ["*"]

    async with async_session_maker() as db:
        rows = (await db.execute(
            select(DailyPrice.ticker, DailyPrice.date, DailyPrice.close, DailyPrice.adjusted_close)
            .where(DailyPrice.ticker.in_(symbols),
                   DailyPrice.date >= target - timedelta(days=PRICE_LOOKBACK_DAYS),
                   DailyPrice.date <= target)
            .order_by(DailyPrice.ticker, DailyPrice.date)
        )).all()
    series: dict[str, list[tuple[date, float]]] = {}
    raw: dict[str, dict[date, tuple[float | None, float | None]]] = {}
    for ticker, price_date, close, adjusted in rows:
        effective = number(adjusted) if adjusted is not None else number(close)
        if effective is not None and effective > 0:
            series.setdefault(ticker, []).append((price_date, effective))
            raw.setdefault(ticker, {})[price_date] = (number(close), number(adjusted))

    for symbol in symbols:
        observations = _trailing_session_prices(series.get(symbol, []), target)
        if not observations:
            evidence["unavailable"].append(symbol)
            continue
        live = (live_quotes or {}).get(symbol)
        includes_live = False
        if live and live_date and observations[-1][0] == target:
            stored_close, stored_adjusted = raw[symbol][target]
            if _lines_up(stored_close, stored_adjusted, live):
                observations = [*observations, (live_date, float(live["price"]))]
                includes_live = True
        closes = [close for _, close in observations]
        item: dict[str, Any] = {
            "as_of": observations[-1][0].isoformat(), "includes_live_quote": includes_live,
            "close": round(closes[-1], 4), "rsi": _rsi(closes),
        }
        if len(closes) >= MIN_HIGH_LOW_SESSIONS:
            window = closes[-HIGH_LOW_SESSIONS:]
            item.update(high_52w=round(max(window), 4), low_52w=round(min(window), 4))
        if item["rsi"] is None and "high_52w" not in item:
            evidence["unavailable"].append(symbol)
            continue
        evidence["items"][symbol] = item
    return evidence


async def collect_watchlist_signals(context: dict, report_type: str) -> dict[str, Any]:
    """Tags for the report's core/watchlist rows; never raises."""
    core = [row for row in context.get("quotes", []) if row.get("group") == "core"]
    report_date = date.fromisoformat(context["report_date"])
    live_quotes = None
    if report_type == "post_market_summary":
        live_quotes = {row["ticker"]: row for row in core
                       if row.get("fresh") and row.get("session_date") == context["report_date"]
                       and row.get("basis") == "delayed_snapshot"}
    try:
        return await price_signals(
            [row["ticker"] for row in core],
            latest_completed_us_session(report_date),
            live_quotes=live_quotes,
            live_date=report_date if live_quotes else None,
        )
    except Exception as exc:
        logger.warning("Watchlist signals unavailable (%s)", type(exc).__name__)
        return {"items": {}, "error": type(exc).__name__}


def signal_tags(ticker: str, signals: dict | None) -> list[dict[str, str]]:
    """Coloured tags for one symbol. Tag colour marks the signal type, not advice."""
    item = ((signals or {}).get("items") or {}).get(ticker)
    if not item:
        return []
    tags = []
    rsi = number(item.get("rsi"))
    if rsi is not None:
        if rsi <= number(signals.get("oversold", 30)):
            tags.append({"text": f"RSI {rsi:.0f} 超卖", "color": "blue"})
        elif rsi >= number(signals.get("overbought", 70)):
            tags.append({"text": f"RSI {rsi:.0f} 超买", "color": "orange"})
    close, high, low = number(item.get("close")), number(item.get("high_52w")), number(item.get("low_52w"))
    near = number(signals.get("near_extreme_pct", NEAR_EXTREME_PCT)) / 100
    if close and high and close >= high * (1 - near):
        tags.append({"text": "创52周新高" if close >= high else "近52周新高", "color": "green"})
    elif close and low and close <= low * (1 + near):
        tags.append({"text": "创52周新低" if close <= low else "近52周新低", "color": "red"})
    return tags
