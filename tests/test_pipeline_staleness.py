"""Regressions for the market-breadth and index-valuation publications that went stale in 2026-09."""

from datetime import date, timedelta

import pytest

from core.trading_calendar import is_us_market_session, latest_completed_us_session
from services import split_history


def test_carter_day_of_mourning_is_not_a_session():
    # exchange_calendars 4.5.x still lists 2025-01-09 as a session; EODHD has no bars for it,
    # so treating it as one failed every price-history coverage gate.
    assert not is_us_market_session(date(2025, 1, 9))
    assert is_us_market_session(date(2025, 1, 8))
    assert latest_completed_us_session(date(2025, 1, 10)) == date(2025, 1, 8)


@pytest.mark.asyncio
async def test_scheduled_breadth_publishes_when_backfill_gate_fails(monkeypatch):
    from core import scheduler

    calls = []

    async def latest(dataset):
        return None

    async def backfill(target):
        calls.append("backfill")
        raise RuntimeError("History backfill coverage below 80%: 2.64%")

    async def publish(target):
        calls.append("publish")
        return {"status": "published", "as_of_date": target.isoformat()}

    monkeypatch.setattr(scheduler, "latest_published_date", latest)
    monkeypatch.setattr(scheduler, "backfill_market_breadth_price_history", backfill)
    monkeypatch.setattr(scheduler, "refresh_market_breadth", publish)

    result = await scheduler.scheduled_market_breadth_sync(date(2026, 10, 6))

    assert result["status"] == "published"
    assert calls == ["backfill", "publish"]


@pytest.mark.asyncio
async def test_index_valuation_refreshes_splits_first_and_survives_refresh_failure(monkeypatch):
    from core import scheduler

    calls = []

    async def latest(dataset):
        return date(2026, 9, 18)

    async def refresh_splits(target):
        calls.append("splits")
        raise TimeoutError

    async def refresh_valuation(target):
        calls.append("valuation")
        return {"status": "published"}

    monkeypatch.setattr(scheduler, "latest_published_date", latest)
    monkeypatch.setattr(scheduler, "refresh_member_split_histories", refresh_splits)
    monkeypatch.setattr(scheduler, "refresh_index_valuation", refresh_valuation)

    result = await scheduler.scheduled_index_valuation_sync(date(2026, 10, 6))

    assert result == {"status": "published"}
    assert calls == ["splits", "valuation"]


@pytest.mark.asyncio
async def test_index_valuation_skips_split_refresh_when_already_published(monkeypatch):
    from core import scheduler

    async def latest(dataset):
        return date(2026, 10, 5)

    async def should_not_run(target):
        raise AssertionError("no provider calls once the day is published")

    monkeypatch.setattr(scheduler, "latest_published_date", latest)
    monkeypatch.setattr(scheduler, "refresh_member_split_histories", should_not_run)
    monkeypatch.setattr(scheduler, "refresh_index_valuation", should_not_run)

    result = await scheduler.scheduled_index_valuation_sync(date(2026, 10, 6))
    assert result["reason"] == "already-published"


@pytest.mark.asyncio
async def test_split_refresh_only_fetches_members_whose_snapshot_is_stale(monkeypatch):
    horizons = {"FRESH.US": date(2026, 10, 6), "STALE.US": date(2026, 9, 18), "BROKEN.US": None}
    synced = []

    async def fake_load(db, ticker, start, end):
        through = horizons[ticker]
        return object() if through is not None and through >= end else None

    async def fake_sync(db, ticker):
        synced.append(ticker)
        if ticker == "BROKEN.US":
            return False
        horizons[ticker] = date(2026, 10, 6)
        return True

    monkeypatch.setattr(split_history, "load_split_history", fake_load)
    monkeypatch.setattr(split_history, "sync_full_split_history", fake_sync)

    stats = await split_history.refresh_stale_split_histories({
        "FRESH.US": (date(1990, 1, 2), date(2026, 10, 5)),
        "STALE.US": (date(1990, 1, 2), date(2026, 10, 5)),
        "BROKEN.US": (date(1990, 1, 2), date(2026, 10, 5)),
    })

    assert sorted(synced) == ["BROKEN.US", "STALE.US"]
    assert stats["current"] == 1
    assert stats["refreshed"] == 1
    assert stats["failed_tickers"] == ["BROKEN.US"]


@pytest.mark.asyncio
async def test_split_requirement_matches_valuation_history_window(db_session):
    """The refresh must demand exactly what verification will: prices through the cap
    plus statement vintages fetched after the last price."""
    from datetime import datetime
    from decimal import Decimal

    from models import DailyPrice, FinancialStatement, Ticker
    from services.valuation_history import split_history_requirement

    db_session.add(Ticker(ticker="OLD.US", last_updated=datetime(2026, 9, 30)))
    for day in (date(2015, 3, 2), date(2019, 6, 3), date(2026, 10, 2)):
        db_session.add(DailyPrice(ticker="OLD.US", date=day, close=Decimal(10), adjusted_close=Decimal(10)))
    db_session.add(FinancialStatement(ticker="OLD.US", period="Quarterly", fiscal_date=date(2019, 3, 31)))
    await db_session.commit()

    assert await split_history_requirement("OLD.US", db_session, through=date(2026, 10, 5)) == (date(2015, 3, 2), date(2026, 10, 2))
    capped = await split_history_requirement("OLD.US", db_session, through=date(2019, 12, 31))
    # The statement vintage (profile refresh on 2026-09-30) outlives the capped prices.
    assert capped == (date(2015, 3, 2), date(2026, 9, 30))
