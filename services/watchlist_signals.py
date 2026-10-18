"""Daily-close technical tags for the core assets and watchlist.

Uses the same RSI(14) definition and stored-price refresh as the RSI monitor,
so the tags agree with its alerts. A post-market report may append today's
delayed close, but only when the quote's previous close lines up with the
stored close; a split, distribution or bad tick then falls back to the last
stored session instead of producing a false signal. Only US listings are
tagged because the session walk follows the NYSE calendar.

Consensus-revision and short-interest tags come from each symbol's latest
stored fundamentals payload (services/expectation_signals.py). They are
evaluated when the evidence is collected, so a saved report always renders
the same tags.
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
from services.expectation_signals import MAX_SIGNAL_AGE_DAYS, load_expectation_signals
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


EXPECTATION_FIELDS = (
    "eps_revision_30d", "eps_revision_90d", "eps_next_year_revision_30d", "eps_next_year_revision_90d",
    "eps_revisions_up_30d", "eps_revisions_down_30d", "eps_estimate_analysts",
    "short_interest_change", "short_float",
)


async def expectation_signals(symbols: list[str], reference_date: date) -> dict[str, Any]:
    """Consensus-revision and short-interest evidence per symbol; never raises."""
    evidence: dict[str, Any] = {
        "source": "EODHD fundamentals", "max_age_days": MAX_SIGNAL_AGE_DAYS,
        "reference_date": reference_date.isoformat(), "items": {}, "unavailable": [],
    }
    try:
        async with async_session_maker() as db:
            for symbol in dict.fromkeys(symbols):
                context = await load_expectation_signals(symbol, db, reference_date)
                if not context["available"]:
                    evidence["unavailable"].append(symbol)
                    continue
                values = context["signals"]
                evidence["items"][symbol] = {
                    "as_of": context["as_of"],
                    **{key: values[key] for key in EXPECTATION_FIELDS},
                    "estimate": context["estimate_signal"],
                    "short": context["short_signal"],
                }
    except Exception as exc:
        logger.warning("Expectation signals unavailable (%s)", type(exc).__name__)
        evidence["error"] = type(exc).__name__
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
        signals = await price_signals(
            [row["ticker"] for row in core],
            latest_completed_us_session(report_date),
            live_quotes=live_quotes,
            live_date=report_date if live_quotes else None,
        )
    except Exception as exc:
        logger.warning("Watchlist signals unavailable (%s)", type(exc).__name__)
        signals = {"items": {}, "error": type(exc).__name__}
    signals["expectations"] = await expectation_signals([row["ticker"] for row in core], report_date)
    return signals


def _expectation_tags(item: dict | None) -> list[dict[str, str]]:
    if not item:
        return []
    tags = []
    estimate = item.get("estimate") or {}
    direction = estimate.get("direction")
    if direction in {"cut", "raise"}:
        label = "预期下修" if direction == "cut" else "预期上修"
        basis = estimate.get("basis")
        if basis in {"30d", "90d"}:
            change = number(item.get(f"eps_revision_{basis}"))
            text = f"{label} {abs(change) * 100:.1f}%" if change is not None else label
            text += "·90天" if basis == "90d" else ""
        else:
            count = item.get("eps_revisions_down_30d" if direction == "cut" else "eps_revisions_up_30d")
            text = f"{label} {count}/{item.get('eps_estimate_analysts')}家"
        tags.append({"text": text, "color": "carmine" if direction == "cut" else "turquoise"})
    short = item.get("short") or {}
    if short.get("kind") == "rising" and number(item.get("short_interest_change")) is not None:
        tags.append({"text": f"空头增加 {item['short_interest_change'] * 100:.0f}%", "color": "purple"})
    elif short.get("kind") in {"rising", "crowded"} and number(item.get("short_float")) is not None:
        tags.append({"text": f"空头占流通 {item['short_float'] * 100:.0f}%", "color": "purple"})
    return tags


def _expectation_item(ticker: str, signals: dict | None) -> dict | None:
    return (((signals or {}).get("expectations") or {}).get("items") or {}).get(ticker)


def signal_tags(ticker: str, signals: dict | None) -> list[dict[str, str]]:
    """Coloured tags for one symbol. Tag colour marks the signal type, not advice."""
    return _price_tags(ticker, signals) + _expectation_tags(_expectation_item(ticker, signals))


def signal_as_of(ticker: str, signals: dict | None) -> str:
    """Dates behind a symbol's tags, e.g. '截至 2026-10-16；预期数据截至 2026-10-13'."""
    price = (((signals or {}).get("items") or {}).get(ticker) or {}).get("as_of")
    expectation = _expectation_item(ticker, signals)
    parts = [f"截至 {price}" if price else ""]
    if expectation and _expectation_tags(expectation):
        parts.append(f"预期数据截至 {expectation.get('as_of')}")
    return "；".join(part for part in parts if part)


def _price_tags(ticker: str, signals: dict | None) -> list[dict[str, str]]:
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
