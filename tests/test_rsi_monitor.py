from datetime import date

import pytest
from sqlalchemy import select

from core.config import settings
from models import DailyPrice, RsiAlert, Ticker
from services import rsi_monitor


async def _seed_prices(db_session, ticker: str, closes: list[float], target: date) -> None:
    db_session.add(Ticker(ticker=ticker))
    dates = rsi_monitor._recent_us_sessions(target, len(closes))
    db_session.add_all(
        DailyPrice(
            ticker=ticker,
            date=price_date,
            open=close,
            high=close,
            low=close,
            close=close,
            adjusted_close=close,
            volume=1_000,
        )
        for price_date, close in zip(dates, closes)
    )
    await db_session.commit()


def test_recent_history_requires_consecutive_market_sessions():
    target = date(2025, 7, 7)
    sessions = rsi_monitor._recent_us_sessions(
        target,
        rsi_monitor.RSI_PERIOD + 1,
    )

    assert rsi_monitor._has_sufficient_recent_history(set(sessions), target)
    assert not rsi_monitor._has_sufficient_recent_history(
        set(sessions) - {sessions[-2]},
        target,
    )


@pytest.mark.asyncio
async def test_signal_calculation_rejects_a_gap_before_target(
    db_session,
):
    target = date(2025, 7, 7)
    await _seed_prices(
        db_session,
        "GAP.US",
        [120 - index for index in range(24)],
        target,
    )
    missing_session = rsi_monitor._recent_us_sessions(target, 5)[0]
    row = await db_session.execute(
        select(DailyPrice).where(
            DailyPrice.ticker == "GAP.US",
            DailyPrice.date == missing_session,
        )
    )
    await db_session.delete(row.scalar_one())
    await db_session.commit()

    signals, unavailable = await rsi_monitor._calculate_signals(
        ["GAP.US"],
        target,
    )

    assert signals == []
    assert unavailable == ["GAP.US"]


@pytest.mark.asyncio
async def test_daily_rsi_monitor_alerts_once_per_ticker_and_session(
    db_session,
    monkeypatch,
):
    target = date(2025, 7, 7)
    await _seed_prices(
        db_session,
        "AAPL.US",
        [120 - index for index in range(24)],
        target,
    )
    monkeypatch.setattr(settings, "RSI_MONITOR_SYMBOLS", "AAPL")

    async def no_refresh(symbols, target_date):
        return {}

    notifications = []

    async def broadcast(*, title, content, channels=None):
        notifications.append((title, content))
        return True

    monkeypatch.setattr(rsi_monitor, "_refresh_missing_prices", no_refresh)
    monkeypatch.setattr(
        rsi_monitor.NotificationManager,
        "broadcast",
        staticmethod(broadcast),
    )

    first = await rsi_monitor.run_daily_rsi_monitor(date(2025, 7, 8))
    second = await rsi_monitor.run_daily_rsi_monitor(date(2025, 7, 8))

    assert first["status"] == "alerted"
    assert first["alerts"] == 1
    assert second["status"] == "already-delivered"
    assert len(notifications) == 1
    assert "Quantify" in notifications[0][0]
    assert "AAPL.US" in notifications[0][1]
    assert "超卖" in notifications[0][1]
    alerts = list((await db_session.execute(select(RsiAlert))).scalars())
    assert len(alerts) == 1
    assert alerts[0].ticker == "AAPL.US"
    assert alerts[0].price_date == target
    assert alerts[0].zone == "oversold"


@pytest.mark.asyncio
async def test_daily_rsi_monitor_uses_watchlist_and_ignores_neutral_rsi(
    db_session,
    monkeypatch,
):
    target = date(2025, 7, 7)
    await _seed_prices(
        db_session,
        "MSFT.US",
        [100 + (index % 2) for index in range(24)],
        target,
    )
    from services.personal_workspace import replace_watchlist

    await replace_watchlist(db_session, ["MSFT"])
    monkeypatch.setattr(settings, "RSI_MONITOR_SYMBOLS", "")

    async def no_refresh(symbols, target_date):
        assert symbols == ["MSFT.US"]
        return {}

    async def unexpected_broadcast(**kwargs):
        raise AssertionError("neutral RSI must not send a notification")

    monkeypatch.setattr(rsi_monitor, "_refresh_missing_prices", no_refresh)
    monkeypatch.setattr(
        rsi_monitor.NotificationManager,
        "broadcast",
        staticmethod(unexpected_broadcast),
    )

    result = await rsi_monitor.run_daily_rsi_monitor(date(2025, 7, 8))

    assert result["status"] == "no-alerts"
    assert result["monitored"] == 1
    assert result["signals"] == 0


@pytest.mark.asyncio
async def test_failed_notification_is_not_recorded(db_session, monkeypatch):
    target = date(2025, 7, 7)
    await _seed_prices(
        db_session,
        "NVDA.US",
        [100 + index for index in range(24)],
        target,
    )
    monkeypatch.setattr(settings, "RSI_MONITOR_SYMBOLS", "NVDA.US")

    async def no_refresh(symbols, target_date):
        return {}

    async def rejected_broadcast(**kwargs):
        return False

    monkeypatch.setattr(rsi_monitor, "_refresh_missing_prices", no_refresh)
    monkeypatch.setattr(
        rsi_monitor.NotificationManager,
        "broadcast",
        staticmethod(rejected_broadcast),
    )

    with pytest.raises(RuntimeError, match="no notification channel"):
        await rsi_monitor.run_daily_rsi_monitor(date(2025, 7, 8))

    alerts = list((await db_session.execute(select(RsiAlert))).scalars())
    assert alerts == []
