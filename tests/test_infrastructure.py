import asyncio
import sqlite3
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from models import (
    DailyPrice,
    DataPublication,
    FactorValue,
    FinancialStatement,
    PipelineRun,
    SecurityMaster,
    StockScreenerSnapshot,
    Ticker,
    UniverseMembership,
)
from services.analyzer import batch_get_factor_scores, filter_screener_stocks, get_fundamental_valuation
from services.data_quality import validate_screener_records
from services.raw_store import persist_snapshot
from services.security_master import canonicalize_ticker, upsert_security
from services.universe import record_universe_membership, universe_as_of
from scripts.backup_sqlite import create_backup
from scripts.seed_screener_e2e import assert_safe_e2e_database
from scripts.validate_screener import is_non_finite_numeric
from core.trading_calendar import is_us_market_session, latest_completed_us_session, us_market_close_utc
from services.freshness import assess_ticker_freshness
from services.catchup import catch_up_latest_publications
from core.security import SlidingWindowRateLimiter


@pytest.mark.asyncio
async def test_schema_enables_sqlite_foreign_keys(db_session):
    result = await db_session.execute(text("PRAGMA foreign_keys"))
    assert result.scalar_one() == 1


def test_e2e_seed_refuses_non_test_databases():
    with pytest.raises(RuntimeError, match="refusing to reset"):
        assert_safe_e2e_database(
            "production",
            "sqlite+aiosqlite:///./data/quantify_local.db",
        )
    with pytest.raises(RuntimeError, match="refusing to reset"):
        assert_safe_e2e_database(
            "test",
            "postgresql+asyncpg://test_user@production/quantify",
        )
    with pytest.raises(RuntimeError, match="refusing to reset"):
        assert_safe_e2e_database(
            "test",
            "sqlite+aiosqlite:///./e2e-parent/quantify_local.db",
        )
    assert_safe_e2e_database(
        "test",
        "sqlite+aiosqlite:///./data/screener_e2e.db",
    )


def test_screener_validation_detects_non_finite_decimal_values():
    assert is_non_finite_numeric(Decimal("NaN"))
    assert is_non_finite_numeric(Decimal("Infinity"))
    assert not is_non_finite_numeric(Decimal("1.25"))


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
async def test_universe_membership_supports_same_date_corrections(db_session):
    snapshot_date = date(2025, 1, 2)
    await record_universe_membership(
        db_session,
        "TEST",
        ["AAA.US", "BBB.US"],
        snapshot_date,
    )
    await record_universe_membership(
        db_session,
        "TEST",
        ["BBB.US", "CCC.US"],
        snapshot_date,
    )
    assert set(await universe_as_of(db_session, "TEST", snapshot_date)) == {
        "BBB.US",
        "CCC.US",
    }

    await record_universe_membership(
        db_session,
        "TEST",
        ["AAA.US", "BBB.US", "CCC.US"],
        snapshot_date,
    )
    memberships = (await db_session.execute(
        select(UniverseMembership).where(
            UniverseMembership.universe == "TEST",
        )
    )).scalars().all()
    assert {row.ticker for row in memberships} == {"AAA.US", "BBB.US", "CCC.US"}
    assert all(
        row.effective_to is None or row.effective_to >= row.effective_from
        for row in memberships
    )


@pytest.mark.asyncio
async def test_universe_membership_rejects_partial_observation(db_session):
    initial = [f"T{index}.US" for index in range(10)]
    await record_universe_membership(db_session, "TEST", initial, date(2025, 1, 1))
    await db_session.commit()

    with pytest.raises(ValueError, match="refusing to close memberships"):
        await record_universe_membership(
            db_session,
            "TEST",
            initial[:2],
            date(2025, 1, 2),
            minimum_retained_fraction=0.9,
        )

    assert set(await universe_as_of(db_session, "TEST", date(2025, 1, 2))) == set(initial)


@pytest.mark.asyncio
async def test_universe_membership_allows_verified_exits(db_session):
    initial = [f"T{index}.US" for index in range(10)]
    await record_universe_membership(db_session, "TEST", initial, date(2025, 1, 1))
    await db_session.commit()

    retained = initial[:2]
    await record_universe_membership(
        db_session,
        "TEST",
        retained,
        date(2025, 1, 2),
        minimum_retained_fraction=0.9,
        known_exits=initial[2:],
    )

    assert set(await universe_as_of(db_session, "TEST", date(2025, 1, 2))) == set(
        retained
    )


@pytest.mark.asyncio
async def test_bulk_screener_rejects_partial_target_universe(monkeypatch):
    from services.screener_sync import fetch_and_merge_bulk_data

    @asynccontextmanager
    async def fake_client():
        yield object()

    async def fake_components(index_ticker, client=None):
        prefix = "SP" if index_ticker == "GSPC.INDX" else "RU"
        return [f"{prefix}{index}.US" for index in range(5)]

    async def partial_bulk(*args, **kwargs):
        return [
            {
                "code": ticker,
                "exchange_short_name": "US",
                "date": "2025-01-02",
                "close": 100,
            }
            for ticker in ["SP0", "RU0"]
        ]

    monkeypatch.setattr(
        "services.screener_sync.eodhd_client.create_http_client",
        fake_client,
    )
    monkeypatch.setattr(
        "services.screener_sync.eodhd_client.get_index_components",
        fake_components,
    )

    async def no_delisted_symbols(*args, **kwargs):
        return []

    monkeypatch.setattr(
        "services.screener_sync.eodhd_client.get_exchange_symbol_list",
        no_delisted_symbols,
    )
    monkeypatch.setattr(
        "services.screener_sync.eodhd_client.get_bulk_eod_prices",
        partial_bulk,
    )

    async def empty_actions(*args, **kwargs):
        return []

    monkeypatch.setattr(
        "services.screener_sync.eodhd_client.get_bulk_corporate_actions",
        empty_actions,
    )

    with pytest.raises(ValueError, match="universe coverage"):
        await fetch_and_merge_bulk_data("2025-01-02")


@pytest.mark.asyncio
async def test_bulk_screener_filters_delisted_index_components(monkeypatch):
    from services.screener_sync import fetch_and_merge_bulk_data

    @asynccontextmanager
    async def fake_client():
        yield object()

    async def fake_components(index_ticker, client=None):
        if index_ticker == "GSPC.INDX":
            return ["ACTIVE.US", "STALE.US", "REUSED.US", "SP2.US"]
        return ["RU0.US", "RU1.US"]

    async def fake_delisted_symbols(*args, **kwargs):
        return [
            {"Code": "STALE", "Exchange": "NASDAQ", "Name": "Old Co"},
            {"Code": "REUSED", "Exchange": "NYSE", "Name": "Former Reused Co"},
        ]

    async def full_bulk(*args, **kwargs):
        return [
            {
                "code": ticker,
                "exchange_short_name": "US",
                "date": "2025-01-02",
                "close": 100,
            }
            for ticker in ["ACTIVE", "REUSED", "SP2", "RU0", "RU1", "SPY"]
        ]

    async def empty_actions(*args, **kwargs):
        return []

    async def no_fundamentals(*args, **kwargs):
        return []

    monkeypatch.setattr(
        "services.screener_sync.eodhd_client.create_http_client",
        fake_client,
    )
    monkeypatch.setattr(
        "services.screener_sync.eodhd_client.get_index_components",
        fake_components,
    )
    monkeypatch.setattr(
        "services.screener_sync.eodhd_client.get_exchange_symbol_list",
        fake_delisted_symbols,
    )
    monkeypatch.setattr(
        "services.screener_sync.eodhd_client.get_bulk_eod_prices",
        full_bulk,
    )
    monkeypatch.setattr(
        "services.screener_sync.eodhd_client.get_bulk_corporate_actions",
        empty_actions,
    )
    monkeypatch.setattr(
        "services.screener_sync.fetch_target_universe_fundamentals",
        no_fundamentals,
    )

    frame = await fetch_and_merge_bulk_data("2025-01-02")

    assert set(frame.attrs["target_tickers"]) == {
        "ACTIVE.US",
        "REUSED.US",
        "SP2.US",
        "RU0.US",
        "RU1.US",
    }
    assert set(frame.attrs["sp500_tickers"]) == {
        "ACTIVE.US",
        "REUSED.US",
        "SP2.US",
    }
    assert set(frame.attrs["russell2000_tickers"]) == {"RU0.US", "RU1.US"}
    assert set(frame.attrs["known_exits"]) == {"STALE.US"}
    assert set(frame.attrs["sp500_known_exits"]) == {"STALE.US"}
    assert set(frame.attrs["russell2000_known_exits"]) == set()
    assert frame.attrs["universe_coverage"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_bulk_screener_rejects_an_incomplete_index_feed(monkeypatch):
    from services.screener_sync import fetch_and_merge_bulk_data

    @asynccontextmanager
    async def fake_client():
        yield object()

    async def fake_components(index_ticker, client=None):
        if index_ticker == "GSPC.INDX":
            return ["SP0.US", "SP1.US"]
        return []

    monkeypatch.setattr(
        "services.screener_sync.eodhd_client.create_http_client",
        fake_client,
    )
    monkeypatch.setattr(
        "services.screener_sync.eodhd_client.get_index_components",
        fake_components,
    )

    with pytest.raises(ValueError, match="Russell 2000 component universe is too small"):
        await fetch_and_merge_bulk_data("2025-01-02")


@pytest.mark.asyncio
async def test_bulk_screener_preserves_raw_batches_and_filters_actions(monkeypatch):
    from services.screener_sync import fetch_and_merge_bulk_data

    @asynccontextmanager
    async def fake_client():
        yield object()

    bulk_prices = [
        {
            "code": ticker,
            "exchange_short_name": "US",
            "date": "2025-01-02",
            "close": 100,
        }
        for ticker in ["AAA", "BBB", "SPY"]
    ]
    raw_splits = [
        {"code": "AAA", "exchange": "US", "date": "2025-01-02", "split": "2/1"},
        {"code": "OUT", "exchange": "US", "date": "2025-01-02", "split": "3/1"},
    ]
    raw_dividends = [
        {"code": "BBB", "exchange": "US", "date": "2025-01-02", "dividend": 0.25},
    ]

    async def fake_bulk_prices(*args, **kwargs):
        return bulk_prices

    async def fake_bulk_actions(action_type, *args, **kwargs):
        return raw_splits if action_type == "splits" else raw_dividends

    async def no_fundamentals(*args, **kwargs):
        return []

    monkeypatch.setattr(
        "services.screener_sync.eodhd_client.create_http_client",
        fake_client,
    )
    monkeypatch.setattr(
        "services.screener_sync.eodhd_client.get_bulk_eod_prices",
        fake_bulk_prices,
    )
    monkeypatch.setattr(
        "services.screener_sync.eodhd_client.get_bulk_corporate_actions",
        fake_bulk_actions,
    )
    monkeypatch.setattr(
        "services.screener_sync.fetch_target_universe_fundamentals",
        no_fundamentals,
    )

    frame = await fetch_and_merge_bulk_data(
        "2025-01-02",
        target_tickers={"AAA.US", "BBB.US"},
    )

    assert frame.attrs["raw_bulk_eod"] == bulk_prices
    assert frame.attrs["raw_bulk_splits"] == raw_splits
    assert frame.attrs["raw_bulk_dividends"] == raw_dividends
    assert frame.attrs["benchmark_prices"] == [{**bulk_prices[2], "ticker": "SPY.US"}]
    assert {item["ticker"] for item in frame.attrs["bulk_splits"]} == {"AAA.US"}
    assert {item["ticker"] for item in frame.attrs["bulk_dividends"]} == {"BBB.US"}


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


@pytest.mark.asyncio
async def test_screener_does_not_expose_unpublished_requested_snapshot(db_session):
    db_session.add(Ticker(ticker="AAA.US"))
    db_session.add(
        StockScreenerSnapshot(
            ticker="AAA.US",
            date=date(2025, 1, 3),
            close=30,
            market_cap=300,
        )
    )
    await db_session.commit()

    result = await filter_screener_stocks(
        {"as_of_date": date(2025, 1, 3), "limit": 50, "offset": 0},
        db_session,
    )

    assert result["as_of_date"] == "2025-01-03"
    assert result["total"] == 0
    assert result["items"] == []


@pytest.mark.asyncio
async def test_screener_keeps_published_snapshot_point_in_time(db_session, monkeypatch):
    async def latest_valuation_must_not_be_used(*args, **kwargs):
        raise AssertionError("published snapshots must not use mutable latest fundamentals")

    monkeypatch.setattr(
        "services.analyzer.get_fundamental_valuation",
        latest_valuation_must_not_be_used,
    )
    db_session.add(Ticker(ticker="AAA.US"))
    db_session.add(
        StockScreenerSnapshot(
            ticker="AAA.US",
            date=date(2025, 1, 2),
            close=20,
            market_cap=None,
            pe_ratio=None,
        )
    )
    run = PipelineRun(pipeline_name="test", target_date=date(2025, 1, 2), status="published")
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        DataPublication(
            dataset="screener",
            as_of_date=date(2025, 1, 2),
            pipeline_run_id=run.id,
        )
    )
    await db_session.commit()

    result = await filter_screener_stocks(
        {"as_of_date": date(2025, 1, 2), "limit": 50, "offset": 0},
        db_session,
    )

    assert result["total"] == 1
    assert result["items"][0]["market_cap"] is None
    assert result["items"][0]["pe_ratio"] is None


@pytest.mark.asyncio
async def test_factor_api_hides_rows_without_matching_publication(db_session):
    from api.routers import read_factors

    as_of = date(2025, 1, 2)
    failed_run = PipelineRun(
        pipeline_name="factor_cross_section",
        target_date=as_of,
        status="failed",
    )
    db_session.add(failed_run)
    await db_session.flush()
    db_session.add(FactorValue(
        ticker="AAA.US",
        as_of_date=as_of,
        factor_name="composite",
        normalized_value=1.0,
        version="lfq-v1",
        available_at=datetime(2025, 1, 2, 23, 0),
        source_run_id=failed_run.id,
    ))
    await db_session.commit()

    assert await read_factors(as_of, db=db_session) == []

    failed_run.status = "published"
    db_session.add(DataPublication(
        dataset="factors",
        as_of_date=as_of,
        pipeline_run_id=failed_run.id,
    ))
    await db_session.commit()
    rows = await read_factors(as_of, db=db_session)
    assert [row["ticker"] for row in rows] == ["AAA.US"]


@pytest.mark.asyncio
async def test_quant_coverage_only_counts_published_factor_snapshots(db_session):
    from api.routers import read_quant_coverage

    published_date = date(2025, 1, 2)
    published_run = PipelineRun(
        pipeline_name="factor_cross_section",
        target_date=published_date,
        status="published",
    )
    unpublished_run = PipelineRun(
        pipeline_name="factor_cross_section",
        target_date=date(2025, 1, 3),
        status="failed",
    )
    db_session.add_all([published_run, unpublished_run])
    await db_session.flush()
    db_session.add_all([
        DataPublication(
            dataset="factors",
            as_of_date=published_date,
            pipeline_run_id=published_run.id,
            published_at=datetime(2025, 1, 2, 23, 0),
        ),
        DataPublication(
            dataset="factors",
            as_of_date=date(2025, 1, 3),
            pipeline_run_id=unpublished_run.id,
            published_at=datetime(2025, 1, 3, 23, 0),
        ),
        FactorValue(
            ticker="AAA.US",
            as_of_date=published_date,
            factor_name="composite",
            raw_value=2.0,
            normalized_value=1.0,
            version="lfq-v1",
            available_at=datetime(2025, 1, 2, 22, 0),
            source_run_id=published_run.id,
        ),
        FactorValue(
            ticker="BBB.US",
            as_of_date=published_date,
            factor_name="value",
            raw_value=1.0,
            normalized_value=0.5,
            version="lfq-v1",
            available_at=datetime(2025, 1, 2, 22, 0),
            source_run_id=published_run.id,
        ),
        FactorValue(
            ticker="SHADOW.US",
            as_of_date=date(2025, 1, 3),
            factor_name="composite",
            normalized_value=9.0,
            version="lfq-v1",
            available_at=datetime(2025, 1, 3, 22, 0),
            source_run_id=unpublished_run.id,
        ),
    ])
    await db_session.commit()

    result = await read_quant_coverage(db=db_session)

    assert result["publications"]["factors"]["as_of_date"] == "2025-01-02"
    assert result["factors"] == {
        "min_date": "2025-01-02",
        "max_date": "2025-01-02",
        "date_count": 1,
        "ticker_count": 2,
        "names": ["composite", "value"],
    }


@pytest.mark.asyncio
async def test_latest_ticker_factors_uses_latest_published_run(db_session):
    from api.routers import read_latest_ticker_factors

    as_of = date(2025, 1, 2)
    run = PipelineRun(
        pipeline_name="factor_cross_section",
        target_date=as_of,
        status="published",
    )
    failed_run = PipelineRun(
        pipeline_name="factor_cross_section",
        target_date=date(2025, 1, 3),
        status="failed",
    )
    db_session.add_all([run, failed_run])
    await db_session.flush()
    db_session.add_all([
        DataPublication(dataset="factors", as_of_date=as_of, pipeline_run_id=run.id),
        DataPublication(dataset="factors", as_of_date=date(2025, 1, 3), pipeline_run_id=failed_run.id),
        FactorValue(
            ticker="AAA.US",
            as_of_date=as_of,
            factor_name="composite",
            raw_value=1.25,
            normalized_value=0.75,
            version="lfq-v1",
            available_at=datetime(2025, 1, 2, 22, 0),
            source_run_id=run.id,
            details={"components": 5},
        ),
        FactorValue(
            ticker="AAA.US",
            as_of_date=date(2025, 1, 3),
            factor_name="composite",
            raw_value=99.0,
            normalized_value=99.0,
            version="lfq-v1",
            available_at=datetime(2025, 1, 3, 22, 0),
            source_run_id=failed_run.id,
        ),
    ])
    await db_session.commit()

    result = await read_latest_ticker_factors("aaa", db=db_session)

    assert result["ticker"] == "AAA.US"
    assert result["as_of_date"] == "2025-01-02"
    assert result["version"] == "lfq-v1"
    assert result["factors"]["composite"] == {
        "raw_value": 1.25,
        "normalized_value": 0.75,
        "details": {"components": 5},
    }


@pytest.mark.asyncio
async def test_ticker_sync_locks_are_removed_when_idle():
    from services.sync_coordinator import _ticker_locks, ticker_sync_lock

    for index in range(1_000):
        async with ticker_sync_lock(f"T{index}.US"):
            pass

    assert _ticker_locks == {}


@pytest.mark.asyncio
async def test_ticker_sync_lock_keeps_single_flight_semantics():
    from services.sync_coordinator import _ticker_locks, ticker_sync_lock

    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def first_request():
        async with ticker_sync_lock("AAA.US"):
            first_entered.set()
            await release_first.wait()

    async def second_request():
        await first_entered.wait()
        async with ticker_sync_lock("aaa.us"):
            second_entered.set()

    first_task = asyncio.create_task(first_request())
    second_task = asyncio.create_task(second_request())
    await first_entered.wait()
    await asyncio.sleep(0)

    assert not second_entered.is_set()
    release_first.set()
    await asyncio.gather(first_task, second_task)

    assert second_entered.is_set()
    assert _ticker_locks == {}


@pytest.mark.asyncio
async def test_read_through_cache_miss_consumes_rate_limit(db_session, monkeypatch):
    from types import SimpleNamespace

    from starlette.requests import Request

    from api import routers

    analysis_calls = 0
    limited_clients = []

    async def fake_analysis(*args, **kwargs):
        nonlocal analysis_calls
        analysis_calls += 1
        return None if analysis_calls == 1 else {"profile": {}}

    async def fake_freshness(*args, **kwargs):
        return SimpleNamespace(needs_sync=True)

    async def fake_limit(request):
        limited_clients.append(request.client.host)

    async def fake_sync(*args, **kwargs):
        return True

    async def fake_valuation(*args, **kwargs):
        return {}

    monkeypatch.setattr(routers, "get_analyzed_stock_data", fake_analysis)
    monkeypatch.setattr(routers, "assess_ticker_freshness", fake_freshness)
    monkeypatch.setattr(routers, "limit_expensive_requests", fake_limit)
    monkeypatch.setattr(routers, "sync_ticker_data", fake_sync)
    monkeypatch.setattr(routers, "get_fundamental_valuation", fake_valuation)
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/stocks/AAA.US",
        "headers": [],
        "client": ("127.0.0.1", 12345),
    })

    result = await routers.read_stock_analysis("AAA.US", request, db=db_session)

    assert result["valuation_metrics"] == {}
    assert limited_clients == ["127.0.0.1"]


@pytest.mark.asyncio
async def test_batch_factor_scores_use_constant_database_queries(db_session, monkeypatch):
    tickers = ["AAA.US", "BBB.US"]
    db_session.add_all([Ticker(ticker=ticker) for ticker in tickers])
    for ticker_index, ticker in enumerate(tickers):
        balance_sheet = {
            "totalAssets": 1_000,
            "totalLiab": 400,
            "totalStockholderEquity": 600,
            "commonStockSharesOutstanding": 100,
        }
        for quarter_index in range(4):
            db_session.add(FinancialStatement(
                ticker=ticker,
                fiscal_date=date(2024, 12, 31) - timedelta(days=quarter_index * 90),
                period="Quarterly",
                revenue=100 + ticker_index * 10,
                net_income=10 + ticker_index,
                income_statement={
                    "totalRevenue": 100 + ticker_index * 10,
                    "grossProfit": 50,
                    "netIncome": 10 + ticker_index,
                },
                balance_sheet=balance_sheet,
                cash_flow={"freeCashFlow": 8},
            ))
        for year_index, revenue in enumerate([120, 100]):
            db_session.add(FinancialStatement(
                ticker=ticker,
                fiscal_date=date(2023 - year_index, 12, 31),
                period="Yearly",
                revenue=revenue,
                net_income=10,
                income_statement={"totalRevenue": revenue, "grossProfit": 50},
                balance_sheet=balance_sheet,
            ))
        for day_index in range(60):
            db_session.add(DailyPrice(
                ticker=ticker,
                date=date(2025, 1, 1) + timedelta(days=day_index),
                close=100 + day_index + ticker_index,
                adjusted_close=100 + day_index + ticker_index,
            ))
    await db_session.commit()

    expected = {
        ticker: (await get_fundamental_valuation(ticker, db_session))["factor_scores"]
        for ticker in tickers
    }
    original_execute = db_session.execute
    execute_calls = 0

    async def counted_execute(*args, **kwargs):
        nonlocal execute_calls
        execute_calls += 1
        return await original_execute(*args, **kwargs)

    monkeypatch.setattr(db_session, "execute", counted_execute)
    result = await batch_get_factor_scores(["aaa.us", "BBB.US", "MISSING.US"], db_session)

    assert execute_calls == 2
    assert result[0] == {"ticker": "AAA.US", "factor_scores": expected["AAA.US"]}
    assert result[1] == {"ticker": "BBB.US", "factor_scores": expected["BBB.US"]}
    assert result[2]["factor_scores"] == {
        "value": 0,
        "quality": 0,
        "growth": 0,
        "health": 0,
        "momentum": 0,
    }


@pytest.mark.asyncio
async def test_ws_monitor_backs_off_after_short_clean_disconnect(monkeypatch):
    from services.ws_monitor import WSMonitor

    monitor = WSMonitor()
    connect_calls = 0
    sleeps = []

    async def short_connection():
        nonlocal connect_calls
        connect_calls += 1
        if connect_calls == 3:
            raise asyncio.CancelledError

    async def record_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(monitor, "connect", short_connection)
    monkeypatch.setattr("services.ws_monitor.asyncio.sleep", record_sleep)

    with pytest.raises(asyncio.CancelledError):
        await monitor.start()

    assert sleeps == [1, 2]


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


@pytest.mark.asyncio
async def test_cleanup_funds_uses_sqlite_compatible_exact_match(db_session):
    from scripts.cleanup_funds import clean_database

    db_session.add_all([
        Ticker(ticker="AEGFX.US"),
        Ticker(ticker="AAPL.US"),
        Ticker(ticker="TOOLONGX.US"),
    ])
    db_session.add_all([
        DailyPrice(ticker="AEGFX.US", date=date(2025, 1, 2), close=10),
        FinancialStatement(
            ticker="AEGFX.US",
            fiscal_date=date(2024, 12, 31),
            period="Yearly",
        ),
        StockScreenerSnapshot(ticker="AEGFX.US", date=date(2025, 1, 2), close=10),
    ])
    await db_session.commit()

    await clean_database()
    await db_session.rollback()

    remaining = set((await db_session.execute(select(Ticker.ticker))).scalars())
    assert remaining == {"AAPL.US", "TOOLONGX.US"}
    assert (await db_session.execute(select(DailyPrice))).scalars().all() == []
    assert (await db_session.execute(select(FinancialStatement))).scalars().all() == []
    assert (await db_session.execute(select(StockScreenerSnapshot))).scalars().all() == []


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
        authorized = client.get(
            "/api/operations/pipelines",
            headers={"X-API-Key": "test-secret"},
        )
        assert authorized.status_code == 200


def test_production_without_admin_key_starts_in_read_only_mode(monkeypatch):
    from core.config import Settings
    from core.config import settings
    from main import app

    production = Settings(
        ENVIRONMENT="production",
        ADMIN_API_KEY="",
        _env_file=None,
    )
    assert production.ADMIN_API_KEY == ""

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "")
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200
        disabled = client.post("/api/stocks/AAA.US/sync")
        assert disabled.status_code == 503
        assert disabled.json()["detail"] == "Admin operations are disabled"


@pytest.mark.asyncio
async def test_rate_limiter_purges_expired_client_keys(monkeypatch):
    current_time = [0.0]
    monkeypatch.setattr("core.security.time.monotonic", lambda: current_time[0])
    limiter = SlidingWindowRateLimiter(limit=10, window_seconds=60)

    for index in range(100):
        await limiter.check(f"client-{index}")
    assert len(limiter._requests) == 100

    current_time[0] = 61.0
    await limiter.check("current-client")
    assert set(limiter._requests) == {"current-client"}


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
async def test_worker_refreshes_stale_screener_before_old_universe_dividends(monkeypatch):
    calls = []

    async def latest_publication(dataset):
        return {
            "screener": date(2025, 7, 2),
            "factors": date(2025, 7, 3),
        }.get(dataset)

    async def fake_dividend_backfill(target):
        calls.append(("dividends", target))
        return {"status": "published"}

    async def fake_screener(target_date, observe_current_universe=False):
        calls.append(("screener", target_date, observe_current_universe))
        return {"status": "published"}

    monkeypatch.setattr("services.catchup.latest_published_date", latest_publication)
    monkeypatch.setattr(
        "services.catchup.backfill_latest_screener_dividends_once",
        fake_dividend_backfill,
    )
    monkeypatch.setattr("services.catchup.run_screener_pipeline", fake_screener)

    result = await catch_up_latest_publications(date(2025, 7, 7))

    assert result["dividend_history"] == "published"
    assert calls == [
        ("screener", "2025-07-03", True),
    ]


@pytest.mark.asyncio
async def test_worker_upgrades_dividends_when_screener_is_current(monkeypatch):
    target = date(2025, 7, 3)
    calls = []

    async def latest_publication(dataset):
        return target

    async def fake_dividend_backfill(requested_target):
        calls.append(("dividends", requested_target))
        return {"status": "published"}

    async def fake_refresh(snapshot_date):
        calls.append(("refresh", snapshot_date))
        return 1

    async def should_not_refresh_screener(*args, **kwargs):
        raise AssertionError("current screener must not be republished")

    monkeypatch.setattr("services.catchup.latest_published_date", latest_publication)
    monkeypatch.setattr(
        "services.catchup.backfill_latest_screener_dividends_once",
        fake_dividend_backfill,
    )
    monkeypatch.setattr(
        "services.catchup.run_screener_pipeline",
        should_not_refresh_screener,
    )
    monkeypatch.setattr(
        "services.catchup.refresh_screener_technicals",
        fake_refresh,
    )

    result = await catch_up_latest_publications(date(2025, 7, 7))

    assert result["dividend_history"] == "published"
    assert calls == [("dividends", target), ("refresh", target)]


@pytest.mark.asyncio
async def test_scheduled_jobs_are_publication_idempotent(monkeypatch):
    from core import scheduler

    async def already_published(dataset):
        return date(2025, 7, 3)

    async def should_not_run(*args, **kwargs):
        raise AssertionError("published work must not run again")

    monkeypatch.setattr(scheduler, "latest_published_date", already_published)
    monkeypatch.setattr(scheduler, "run_screener_pipeline", should_not_run)
    monkeypatch.setattr(scheduler, "compute_factors_for_date", should_not_run)
    screener = await scheduler.scheduled_screener_sync(date(2025, 7, 7))
    factors = await scheduler.scheduled_factor_sync(date(2025, 7, 7))
    assert screener["reason"] == "already-published"
    assert factors["reason"] == "already-published"


@pytest.mark.asyncio
async def test_screener_publication_chains_matching_factor_date(monkeypatch):
    from core import scheduler

    publications = {}
    calls = []

    async def latest(dataset):
        return publications.get(dataset)

    async def publish_screener(target_date, observe_current_universe=False):
        target = date.fromisoformat(target_date)
        publications["screener"] = target
        calls.append(("screener", target, observe_current_universe))
        return {"status": "published", "as_of_date": target_date}

    async def publish_factors(target):
        publications["factors"] = target
        calls.append(("factors", target))
        return {"status": "published", "as_of_date": target.isoformat()}

    monkeypatch.setattr(scheduler, "latest_published_date", latest)
    monkeypatch.setattr(scheduler, "run_screener_pipeline", publish_screener)
    monkeypatch.setattr(scheduler, "compute_factors_for_date", publish_factors)

    result = await scheduler.scheduled_screener_sync(date(2025, 7, 7))

    assert result["factors"]["status"] == "published"
    assert calls == [
        ("screener", date(2025, 7, 3), True),
        ("factors", date(2025, 7, 3)),
    ]


@pytest.mark.asyncio
async def test_factor_job_defers_until_matching_screener_is_published(monkeypatch):
    from core import scheduler

    async def latest(dataset):
        return date(2025, 7, 2) if dataset == "screener" else None

    async def should_not_run(*args, **kwargs):
        raise AssertionError("factors must not use a stale screener publication")

    monkeypatch.setattr(scheduler, "latest_published_date", latest)
    monkeypatch.setattr(scheduler, "compute_factors_for_date", should_not_run)

    result = await scheduler.scheduled_factor_sync(date(2025, 7, 7))
    assert result == {
        "status": "deferred",
        "reason": "screener-not-published",
        "as_of_date": "2025-07-03",
    }


def test_compose_initializes_bind_mount_permissions():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text())
    initializer = compose["services"]["data-permissions"]
    backend_dependency = compose["services"]["backend"]["depends_on"]["data-permissions"]

    assert initializer["user"] == "0:0"
    assert "./data:/app/data" in initializer["volumes"]
    assert "chown -R 10001:10001 /app/data" in initializer["command"][-1]
    assert backend_dependency["condition"] == "service_completed_successfully"
    assert "ENVIRONMENT=production" in compose["services"]["backend"]["environment"]
    assert "ENVIRONMENT=production" in compose["services"]["worker"]["environment"]


def test_deploy_waits_for_service_health_and_fails_closed():
    workflow = yaml.safe_load(Path(".github/workflows/deploy.yml").read_text())
    deploy_steps = workflow["jobs"]["deploy"]["steps"]
    deploy_step = next(step for step in deploy_steps if step["name"].startswith("Deploy on Aliyun"))

    assert "script_stop" not in deploy_step["with"]
    script = deploy_step["with"]["script"]
    assert script.startswith("set -eu\n")
    assert "docker compose up -d --remove-orphans" in script
    assert "until curl -fsS http://127.0.0.1:8000/health/ready" in script
    assert "curl -fsS http://127.0.0.1:3000/" in script
    assert 'if [ "$attempt" -ge 60 ]' in script
    assert "docker compose logs --tail=200 backend worker frontend" in script
    assert "exit 1" in script
