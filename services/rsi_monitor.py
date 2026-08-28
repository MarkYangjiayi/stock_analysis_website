from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
import pandas_ta_classic as ta
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from core.config import settings
from core.time_utils import utc_now
from core.trading_calendar import is_us_market_session, latest_completed_us_session
from database import async_session_maker
from models import DailyPrice, RsiAlert, Ticker
from services import eodhd_client
from services.data_sync import _upsert_daily_prices
from services.notifications import NotificationManager
from services.personal_workspace import get_watchlist, normalize_watchlist
from services.raw_store import persist_snapshot


logger = logging.getLogger(__name__)
RSI_PERIOD = 14
PRICE_LOOKBACK_DAYS = 400


@dataclass(frozen=True)
class RsiSignal:
    ticker: str
    price_date: date
    rsi: float
    zone: str
    threshold: float


def _recent_us_sessions(target: date, count: int) -> list[date]:
    sessions: list[date] = []
    cursor = target
    while len(sessions) < count:
        if is_us_market_session(cursor):
            sessions.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(sessions))


def _has_sufficient_recent_history(observed_dates: set[date], target: date) -> bool:
    required = set(_recent_us_sessions(target, RSI_PERIOD + 1))
    return required.issubset(observed_dates)


def _trailing_session_prices(
    observations: list[tuple[date, float]],
    target: date,
) -> list[tuple[date, float]]:
    """Return the uninterrupted XNYS-session suffix ending at target."""
    by_date = dict(observations)
    trailing: list[tuple[date, float]] = []
    cursor = target
    earliest = min(by_date, default=target)
    while cursor >= earliest:
        if is_us_market_session(cursor):
            value = by_date.get(cursor)
            if value is None:
                break
            trailing.append((cursor, value))
        cursor -= timedelta(days=1)
    return list(reversed(trailing))


def _configured_symbols() -> list[str]:
    raw_symbols = [
        symbol
        for symbol in settings.RSI_MONITOR_SYMBOLS.split(",")
        if symbol.strip()
    ]
    return normalize_watchlist(raw_symbols) if raw_symbols else []


async def monitored_symbols() -> list[str]:
    configured = _configured_symbols()
    if configured:
        return configured
    async with async_session_maker() as db:
        return await get_watchlist(db)


async def _refresh_missing_prices(symbols: list[str], target: date) -> dict[str, str]:
    """Refresh only symbols whose local series does not reach the target session."""
    if not symbols:
        return {}
    async with async_session_maker() as db:
        result = await db.execute(
            select(
                DailyPrice.ticker,
                DailyPrice.date,
                DailyPrice.close,
                DailyPrice.adjusted_close,
            ).where(
                DailyPrice.ticker.in_(symbols),
                DailyPrice.date >= target - timedelta(days=45),
                DailyPrice.date <= target,
            )
        )
        recent_rows = result.all()
    observed_dates: dict[str, set[date]] = {}
    for ticker, price_date, close, adjusted_close in recent_rows:
        effective_close = adjusted_close if adjusted_close is not None else close
        try:
            valid = float(effective_close) > 0
        except (TypeError, ValueError):
            valid = False
        if valid:
            observed_dates.setdefault(ticker, set()).add(price_date)
    pending = [
        symbol
        for symbol in symbols
        if not _has_sufficient_recent_history(observed_dates.get(symbol, set()), target)
    ]
    if not pending:
        return {}

    async with async_session_maker() as db, db.begin():
        existing_result = await db.execute(select(Ticker.ticker).where(Ticker.ticker.in_(pending)))
        existing = set(existing_result.scalars())
        missing_tickers = [
            {"ticker": symbol}
            for symbol in pending
            if symbol not in existing
        ]
        if missing_tickers:
            await db.execute(
                insert(Ticker)
                .values(missing_tickers)
                .on_conflict_do_nothing(index_elements=["ticker"])
            )

    semaphore = asyncio.Semaphore(max(1, settings.RSI_MONITOR_REFRESH_CONCURRENCY))
    errors: dict[str, str] = {}
    from_date = (target - timedelta(days=PRICE_LOOKBACK_DAYS)).isoformat()

    async with eodhd_client.create_http_client() as client:
        async def refresh(symbol: str) -> None:
            async with semaphore:
                try:
                    prices = await eodhd_client.get_eod_historical_data(
                        symbol,
                        from_date=from_date,
                        to_date=target.isoformat(),
                        client=client,
                    )
                    if not prices:
                        raise ValueError("provider returned no price history")
                    async with async_session_maker() as db, db.begin():
                        await persist_snapshot(
                            db,
                            "EODHD",
                            "eod_prices",
                            prices,
                            as_of_date=target,
                            details={
                                "ticker": symbol,
                                "from_date": from_date,
                                "to_date": target.isoformat(),
                                "consumer": "rsi_monitor",
                            },
                        )
                        await _upsert_daily_prices(symbol, prices, db)
                except Exception as exc:
                    errors[symbol] = str(exc)
                    logger.warning("RSI price refresh failed for %s: %s", symbol, exc)

        await asyncio.gather(*(refresh(symbol) for symbol in pending))
    return errors


async def _calculate_signals(symbols: list[str], target: date) -> tuple[list[RsiSignal], list[str]]:
    start = target - timedelta(days=PRICE_LOOKBACK_DAYS)
    async with async_session_maker() as db:
        result = await db.execute(
            select(
                DailyPrice.ticker,
                DailyPrice.date,
                DailyPrice.close,
                DailyPrice.adjusted_close,
            )
            .where(
                DailyPrice.ticker.in_(symbols),
                DailyPrice.date >= start,
                DailyPrice.date <= target,
            )
            .order_by(DailyPrice.ticker, DailyPrice.date)
        )
        rows = result.all()

    prices_by_ticker: dict[str, list[tuple[date, float]]] = {}
    for ticker, price_date, close, adjusted_close in rows:
        effective_close = adjusted_close if adjusted_close is not None else close
        try:
            value = float(effective_close)
        except (TypeError, ValueError):
            continue
        if value > 0:
            prices_by_ticker.setdefault(ticker, []).append((price_date, value))

    signals: list[RsiSignal] = []
    unavailable: list[str] = []
    for ticker in symbols:
        observations = _trailing_session_prices(
            prices_by_ticker.get(ticker, []),
            target,
        )
        if len(observations) <= RSI_PERIOD:
            unavailable.append(ticker)
            continue
        close_series = pd.Series([value for _, value in observations], dtype="float64")
        rsi_series = ta.rsi(close_series, length=RSI_PERIOD)
        if rsi_series is None or rsi_series.empty or pd.isna(rsi_series.iloc[-1]):
            unavailable.append(ticker)
            continue
        current_rsi = float(rsi_series.iloc[-1])
        if current_rsi <= settings.RSI_MONITOR_OVERSOLD:
            signals.append(RsiSignal(
                ticker=ticker,
                price_date=target,
                rsi=current_rsi,
                zone="oversold",
                threshold=settings.RSI_MONITOR_OVERSOLD,
            ))
        elif current_rsi >= settings.RSI_MONITOR_OVERBOUGHT:
            signals.append(RsiSignal(
                ticker=ticker,
                price_date=target,
                rsi=current_rsi,
                zone="overbought",
                threshold=settings.RSI_MONITOR_OVERBOUGHT,
            ))
    return signals, unavailable


async def _not_yet_delivered(signals: list[RsiSignal]) -> list[RsiSignal]:
    if not signals:
        return []
    target = signals[0].price_date
    tickers = [signal.ticker for signal in signals]
    async with async_session_maker() as db:
        result = await db.execute(
            select(RsiAlert.ticker, RsiAlert.zone).where(
                RsiAlert.price_date == target,
                RsiAlert.period == RSI_PERIOD,
                RsiAlert.ticker.in_(tickers),
            )
        )
        delivered = set(result.all())
    return [signal for signal in signals if (signal.ticker, signal.zone) not in delivered]


def _notification_content(signals: list[RsiSignal], unavailable: list[str]) -> str:
    oversold = sorted((signal for signal in signals if signal.zone == "oversold"), key=lambda item: item.rsi)
    overbought = sorted((signal for signal in signals if signal.zone == "overbought"), key=lambda item: item.rsi, reverse=True)
    lines = [f"**数据日期：{signals[0].price_date.isoformat()}｜RSI({RSI_PERIOD})**"]
    if oversold:
        lines.extend(["", f"**超卖（≤ {settings.RSI_MONITOR_OVERSOLD:g}）**"])
        lines.extend(f"- {signal.ticker}: **{signal.rsi:.1f}**" for signal in oversold)
    if overbought:
        lines.extend(["", f"**超买（≥ {settings.RSI_MONITOR_OVERBOUGHT:g}）**"])
        lines.extend(f"- {signal.ticker}: **{signal.rsi:.1f}**" for signal in overbought)
    if unavailable:
        lines.extend(["", f"有 {len(unavailable)} 只股票因当日数据不足未参与判断。"])
    return "\n".join(lines)


async def run_daily_rsi_monitor(reference_date: date | None = None) -> dict:
    if not (
        0 <= settings.RSI_MONITOR_OVERSOLD
        < settings.RSI_MONITOR_OVERBOUGHT
        <= 100
    ):
        raise ValueError(
            "RSI thresholds must satisfy "
            "0 <= RSI_MONITOR_OVERSOLD < RSI_MONITOR_OVERBOUGHT <= 100"
        )

    target = latest_completed_us_session(reference_date or date.today())
    symbols = await monitored_symbols()
    if not symbols:
        logger.info("RSI monitor skipped: no configured symbols or personal watchlist items")
        return {"status": "skipped", "reason": "empty-watchlist", "as_of_date": target.isoformat()}

    refresh_errors = await _refresh_missing_prices(symbols, target)
    signals, unavailable = await _calculate_signals(symbols, target)
    pending = await _not_yet_delivered(signals)
    if not pending:
        return {
            "status": "no-alerts" if not signals else "already-delivered",
            "as_of_date": target.isoformat(),
            "monitored": len(symbols),
            "signals": len(signals),
            "unavailable": len(unavailable),
            "refresh_failures": len(refresh_errors),
        }

    delivered = await NotificationManager.broadcast(
        title="📈 Quantify Watchlist 日线 RSI 提醒",
        content=_notification_content(pending, unavailable),
    )
    if not delivered:
        raise RuntimeError("RSI signals were detected but no notification channel accepted the alert")

    async with async_session_maker() as db, db.begin():
        db.add_all(RsiAlert(
            ticker=signal.ticker,
            price_date=signal.price_date,
            period=RSI_PERIOD,
            rsi_value=signal.rsi,
            zone=signal.zone,
            threshold=signal.threshold,
            notified_at=utc_now(),
        ) for signal in pending)
    return {
        "status": "alerted",
        "as_of_date": target.isoformat(),
        "monitored": len(symbols),
        "alerts": len(pending),
        "unavailable": len(unavailable),
        "refresh_failures": len(refresh_errors),
    }
