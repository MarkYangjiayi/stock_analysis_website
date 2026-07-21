import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from models import DailyPrice, DataPublication, PipelineRun, SecurityMaster, StockScreenerSnapshot, Ticker
from services.analyzer import filter_screener_stocks
from services.data_quality import validate_screener_records
from services.raw_store import persist_snapshot
from services.security_master import canonicalize_ticker, upsert_security
from services.universe import record_universe_membership, universe_as_of
from scripts.backup_sqlite import create_backup
from core.trading_calendar import is_us_market_session, latest_completed_us_session, us_market_close_utc
from services.freshness import assess_ticker_freshness
from services.catchup import catch_up_latest_publications


@pytest.mark.asyncio
async def test_schema_enables_sqlite_foreign_keys(db_session):
    result = await db_session.execute(text("PRAGMA foreign_keys"))
    assert result.scalar_one() == 1


@pytest.mark.asyncio
async def test_raw_snapshot_is_immutable_and_deduplicated(db_session):
    first = await persist_snapshot(db_session, "TEST", "prices", [{"x": 1}], date(2025, 1, 1), {"ticker": "AAA.US"})
    second = await persist_snapshot(db_session, "TEST", "prices", [{"x": 1}], date(2025, 1, 1), {"ticker": "AAA.US"})
    await db_session.commit()
    assert first.id == second.id
    assert first.checksum == second.checksum


@pytest.mark.asyncio
async def test_universe_membership_closes_exits(db_session):
    await record_universe_membership(db_session, "TEST", ["AAA.US", "BBB.US"], date(2025, 1, 1))
    await db_session.commit()
    await record_universe_membership(db_session, "TEST", ["BBB.US", "CCC.US"], date(2025, 2, 1))
    await db_session.commit()
    assert set(await universe_as_of(db_session, "TEST", date(2025, 1, 15))) == {"AAA.US", "BBB.US"}
    assert set(await universe_as_of(db_session, "TEST", date(2025, 2, 2))) == {"BBB.US", "CCC.US"}


@pytest.mark.asyncio
async def test_screener_defaults_to_latest_published_snapshot(db_session):
    db_session.add(Ticker(ticker="AAA.US"))
    db_session.add_all([
        StockScreenerSnapshot(ticker="AAA.US", date=date(2025, 1, 1), close=10, market_cap=100),
        StockScreenerSnapshot(ticker="AAA.US", date=date(2025, 1, 2), close=20, market_cap=200),
    ])
    run = PipelineRun(pipeline_name="test", target_date=date(2025, 1, 2), status="published")
    db_session.add(run)
    await db_session.flush()
    db_session.add(DataPublication(dataset="screener", as_of_date=date(2025, 1, 2), pipeline_run_id=run.id))
    await db_session.commit()

    result = await filter_screener_stocks({"limit": 50, "offset": 0}, db_session)
    assert result["total"] == 1
    assert result["as_of_date"] == "2025-01-02"
    assert result["items"][0]["close"] == 20.0


def test_quality_gate_detects_duplicates_and_low_coverage():
    failed = validate_screener_records([
        {"ticker": "AAA.US", "close": 10},
        {"ticker": "AAA.US", "close": None},
    ])
    assert not failed.passed
    assert failed.errors
    passed = validate_screener_records([
        {"ticker": "AAA.US", "close": 10, "market_cap": 100},
        {"ticker": "BBB.US", "close": 20, "market_cap": 200},
    ])
    assert passed.passed


def test_canonical_ticker_policy():
    assert canonicalize_ticker("aapl") == "AAPL.US"
    assert canonicalize_ticker("7203.TSE") == "7203.TSE"


def test_online_backup_is_consistent(tmp_path):
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE values_table (value INTEGER)")
        connection.execute("INSERT INTO values_table VALUES (42)")
    backup = create_backup(source, tmp_path / "backups", retention=2)
    assert not Path(f"{backup}-wal").exists()
    assert not Path(f"{backup}-shm").exists()
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT value FROM values_table").fetchone()[0] == 42
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_exchange_calendar_skips_weekends_and_holidays():
    assert not is_us_market_session(date(2025, 7, 4))
    assert not is_us_market_session(date(2025, 7, 5))
    assert is_us_market_session(date(2025, 7, 7))
    assert latest_completed_us_session(date(2025, 7, 7)) == date(2025, 7, 3)
    assert us_market_close_utc(date(2025, 7, 7)) == datetime(2025, 7, 7, 20, 0)


@pytest.mark.asyncio
async def test_read_through_freshness_uses_completed_session_and_asset_type(db_session):
    db_session.add(Ticker(ticker="SPY.US", last_updated=datetime(2025, 7, 7, 12, 0)))
    db_session.add(SecurityMaster(canonical_ticker="SPY.US", asset_type="ETF"))
    db_session.add(DailyPrice(ticker="SPY.US", date=date(2025, 7, 3), close=600, adjusted_close=600))
    await db_session.commit()

    fresh = await assess_ticker_freshness(db_session, "SPY.US", reference_date=date(2025, 7, 7))
    assert fresh.expected_price_date == date(2025, 7, 3)
    assert fresh.reason == "fresh"

    price = (await db_session.execute(select(DailyPrice).where(DailyPrice.ticker == "SPY.US"))).scalar_one()
    price.date = date(2025, 7, 2)
    await db_session.commit()
    stale = await assess_ticker_freshness(db_session, "SPY.US", reference_date=date(2025, 7, 7))
    assert stale.needs_sync
    assert stale.reason == "price_stale"


@pytest.mark.asyncio
async def test_security_master_deduplicates_bare_and_vendor_symbol(db_session):
    first = await upsert_security(db_session, "AAPL", "NASDAQ", "Apple")
    second = await upsert_security(db_session, "AAPL.US", "NASDAQ", "Apple")
    assert first.id == second.id
    assert first.canonical_ticker == "AAPL.US"


def test_health_and_admin_authentication():
    from main import app

    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200
        unauthorized = client.post("/api/stocks/AAA.US/sync")
        assert unauthorized.status_code == 401


@pytest.mark.asyncio
async def test_worker_catchup_publishes_only_latest_completed_session(monkeypatch):
    calls = []

    async def no_publication(dataset):
        return None

    async def fake_screener(target_date, observe_current_universe=False):
        calls.append(("screener", target_date, observe_current_universe))
        return {"status": "published"}

    async def fake_factors():
        calls.append(("factors",))
        return {"status": "published"}

    monkeypatch.setattr("services.catchup.latest_published_date", no_publication)
    monkeypatch.setattr("services.catchup.run_screener_pipeline", fake_screener)
    monkeypatch.setattr("services.catchup.compute_latest_factors", fake_factors)
    result = await catch_up_latest_publications(date(2025, 7, 7))
    assert result["target_date"] == "2025-07-03"
    assert calls == [("screener", "2025-07-03", True), ("factors",)]


@pytest.mark.asyncio
async def test_scheduled_jobs_are_publication_idempotent(monkeypatch):
    from core import scheduler

    async def already_published(dataset):
        return date(2025, 7, 3)

    async def should_not_run(*args, **kwargs):
        raise AssertionError("published work must not run again")

    monkeypatch.setattr(scheduler, "latest_published_date", already_published)
    monkeypatch.setattr(scheduler, "run_screener_pipeline", should_not_run)
    monkeypatch.setattr(scheduler, "compute_latest_factors", should_not_run)
    screener = await scheduler.scheduled_screener_sync(date(2025, 7, 7))
    factors = await scheduler.scheduled_factor_sync(date(2025, 7, 7))
    assert screener["reason"] == "already-published"
    assert factors["reason"] == "already-published"
