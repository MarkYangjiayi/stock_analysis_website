import asyncio
from datetime import date, timedelta

import pytest
import pandas as pd
from sqlalchemy import func, select

from core.trading_calendar import is_us_market_session
from models import (
    DataPublication,
    CorporateAction,
    DailyPrice,
    FactorValue,
    PipelineRun,
    RawDataSnapshot,
    SignalSnapshot,
    StockScreenerSnapshot,
    StrategyDefinition,
    Ticker,
    UniverseMembership,
)
from services.history_backfill import (
    DIVIDEND_HISTORY_DATASET,
    backfill_dividend_history_once,
    backfill_price_history,
)
from services.quant.backtest import BacktestConfig, run_and_store_backtest
from services.quant.factor_engine import FACTOR_VERSION, compute_and_store_factors
from services.screener_sync import run_screener_pipeline


def preceding_market_sessions(target: date, count: int) -> list[date]:
    sessions = []
    cursor = target - timedelta(days=1)
    while len(sessions) < count:
        if is_us_market_session(cursor):
            sessions.append(cursor)
        cursor -= timedelta(days=1)
    return sessions


@pytest.mark.asyncio
async def test_resumable_history_backfill_with_mocked_provider(db_session, monkeypatch):
    target = date(2025, 1, 10)
    db_session.add_all([Ticker(ticker="AAA.US"), Ticker(ticker="BBB.US")])
    for price_date in preceding_market_sessions(target, 6):
        db_session.add(DailyPrice(
            ticker="AAA.US",
            date=price_date,
            close=10,
            adjusted_close=10,
            volume=1_000,
        ))
    await db_session.commit()

    async def fake_prices(ticker, from_date=None, to_date=None, **kwargs):
        assert ticker == "BBB.US"
        return [
            {
                "date": price_date.isoformat(),
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10,
                "adjusted_close": 10,
                "volume": 1_000,
            }
            for price_date in preceding_market_sessions(target, 6)
        ]

    monkeypatch.setattr("services.history_backfill.eodhd_client.get_eod_historical_data", fake_prices)
    result = await backfill_price_history(
        ["AAA.US", "BBB.US"], history_days=5, target_date=target, include_corporate_actions=False
    )
    assert result["status"] == "published"
    assert result["skipped"] == 1
    assert result["succeeded"] == 1
    count = (await db_session.execute(select(func.count(DailyPrice.id)))).scalar_one()
    assert count == 12
    publication = (await db_session.execute(
        select(DataPublication).where(DataPublication.dataset == "price_history")
    )).scalar_one()
    assert publication.as_of_date == target


@pytest.mark.asyncio
async def test_history_backfill_rejects_one_row_as_complete(db_session, monkeypatch):
    target = date(2025, 1, 10)
    db_session.add(Ticker(ticker="AAA.US"))
    await db_session.commit()

    async def one_price(*args, **kwargs):
        return [{
            "date": target.isoformat(),
            "close": 10,
            "adjusted_close": 10,
            "volume": 100,
        }]

    monkeypatch.setattr(
        "services.history_backfill.eodhd_client.get_eod_historical_data",
        one_price,
    )
    with pytest.raises(RuntimeError, match="coverage below 90%"):
        await backfill_price_history(
            ["AAA.US"],
            history_days=10,
            target_date=target,
            include_corporate_actions=False,
        )

    assert (await db_session.execute(
        select(DataPublication).where(DataPublication.dataset == "price_history")
    )).scalar_one_or_none() is None
    failed_run = (await db_session.execute(
        select(PipelineRun).where(PipelineRun.pipeline_name == "price_history_backfill")
    )).scalar_one()
    assert failed_run.status == "failed"


@pytest.mark.asyncio
async def test_history_backfill_fetches_when_cache_only_meets_quality_tolerance(
    db_session,
    monkeypatch,
):
    target = date(2025, 1, 10)
    db_session.add(Ticker(ticker="AAA.US"))
    for offset in range(10):
        db_session.add(DailyPrice(
            ticker="AAA.US",
            date=target - timedelta(days=offset),
            close=10,
            adjusted_close=10,
        ))
    await db_session.commit()
    calls = []

    async def full_history(ticker, *args, **kwargs):
        calls.append(ticker)
        return [
            {
                "date": price_date.isoformat(),
                "close": 10,
                "adjusted_close": 10,
            }
            for price_date in preceding_market_sessions(target, 11)
        ]

    monkeypatch.setattr(
        "services.history_backfill.eodhd_client.get_eod_historical_data",
        full_history,
    )
    result = await backfill_price_history(
        ["AAA.US"],
        history_days=10,
        target_date=target,
        include_corporate_actions=False,
    )

    assert calls == ["AAA.US"]
    assert result["succeeded"] == 1
    assert result["full_window_rows"] == 11


@pytest.mark.asyncio
async def test_history_backfill_fetches_when_latest_session_is_missing(
    db_session,
    monkeypatch,
):
    target = date(2025, 1, 10)
    db_session.add(Ticker(ticker="AAA.US"))
    for offset in range(3, 14):
        db_session.add(DailyPrice(
            ticker="AAA.US",
            date=target - timedelta(days=offset),
            close=10,
            adjusted_close=10,
        ))
    await db_session.commit()
    calls = []

    async def current_history(ticker, *args, **kwargs):
        calls.append(ticker)
        return [
            {
                "date": price_date.isoformat(),
                "close": 10,
                "adjusted_close": 10,
            }
            for price_date in preceding_market_sessions(target, 11)
        ]

    monkeypatch.setattr(
        "services.history_backfill.eodhd_client.get_eod_historical_data",
        current_history,
    )
    result = await backfill_price_history(
        ["AAA.US"],
        history_days=10,
        target_date=target,
        include_corporate_actions=False,
    )

    assert calls == ["AAA.US"]
    assert result["succeeded"] == 1
    assert result["latest_acceptable_date"] == "2025-01-09"


@pytest.mark.asyncio
async def test_history_backfill_fetches_when_an_internal_session_is_missing(
    db_session,
    monkeypatch,
):
    target = date(2025, 1, 10)
    db_session.add(Ticker(ticker="AAA.US"))
    cached_sessions = preceding_market_sessions(target, 12)
    cached_sessions.pop(5)
    for price_date in cached_sessions:
        db_session.add(DailyPrice(
            ticker="AAA.US",
            date=price_date,
            close=10,
            adjusted_close=10,
        ))
    await db_session.commit()
    calls = []

    async def current_history(ticker, *args, **kwargs):
        calls.append(ticker)
        return [
            {
                "date": price_date.isoformat(),
                "close": 10,
                "adjusted_close": 10,
            }
            for price_date in preceding_market_sessions(target, 11)
        ]

    monkeypatch.setattr(
        "services.history_backfill.eodhd_client.get_eod_historical_data",
        current_history,
    )
    result = await backfill_price_history(
        ["AAA.US"],
        history_days=10,
        target_date=target,
        include_corporate_actions=False,
    )

    assert calls == ["AAA.US"]
    assert result["succeeded"] == 1


@pytest.mark.asyncio
async def test_history_backfill_syncs_actions_when_prices_are_complete(db_session, monkeypatch):
    target = date(2025, 1, 10)
    db_session.add(Ticker(ticker="AAA.US"))
    for price_date in preceding_market_sessions(target, 11):
        db_session.add(DailyPrice(
            ticker="AAA.US",
            date=price_date,
            close=10,
            adjusted_close=10,
        ))
    await db_session.commit()

    async def prices_must_not_run(*args, **kwargs):
        raise AssertionError("complete prices should be skipped")

    async def fake_splits(*args, **kwargs):
        return [{"date": target.isoformat(), "split": "2/1"}]

    async def fake_dividends(*args, **kwargs):
        return [{"date": target.isoformat(), "dividend": "0.25", "currency": "USD"}]

    monkeypatch.setattr(
        "services.history_backfill.eodhd_client.get_eod_historical_data",
        prices_must_not_run,
    )
    monkeypatch.setattr("services.history_backfill.eodhd_client.get_splits", fake_splits)
    monkeypatch.setattr("services.history_backfill.eodhd_client.get_dividends", fake_dividends)

    result = await backfill_price_history(
        ["AAA.US"], history_days=10, target_date=target, include_corporate_actions=True
    )

    assert result["skipped"] == 1
    assert result["corporate_actions"] == 2
    assert (await db_session.execute(select(func.count(CorporateAction.id)))).scalar_one() == 2
    dividend_snapshot = (await db_session.execute(
        select(RawDataSnapshot).where(RawDataSnapshot.dataset == "dividends")
    )).scalar_one()
    assert dividend_snapshot.details["from_date"] == (target - timedelta(days=365 * 7)).isoformat()


@pytest.mark.asyncio
async def test_dividend_history_upgrade_is_retry_safe_and_versioned(db_session, monkeypatch):
    target = date(2025, 1, 10)
    db_session.add_all([Ticker(ticker="AAA.US"), Ticker(ticker="BBB.US")])
    await db_session.commit()
    calls = []

    async def fake_dividends(ticker, from_date=None, to_date=None, **kwargs):
        calls.append((ticker, from_date, to_date))
        return [{"date": "2020-03-01", "dividend": "0.25", "currency": "USD"}]

    monkeypatch.setattr(
        "services.history_backfill.eodhd_client.get_dividends",
        fake_dividends,
    )

    result = await backfill_dividend_history_once(
        ["AAA.US", "BBB.US"],
        target_date=target,
    )
    repeated = await backfill_dividend_history_once(
        ["AAA.US", "BBB.US"],
        target_date=target,
    )
    entrant = await backfill_dividend_history_once(
        ["AAA.US", "BBB.US", "CCC.US"],
        target_date=target + timedelta(days=1),
    )
    recovered = await backfill_dividend_history_once(
        ["AAA.US", "BBB.US", "CCC.US"],
        target_date=target + timedelta(days=10),
        required_through_date=target + timedelta(days=10),
    )

    assert result["status"] == "published"
    assert repeated["reason"] == "already-published"
    assert entrant["status"] == "published"
    assert entrant["attempted"] == 1
    assert recovered["status"] == "published"
    assert recovered["attempted"] == 3
    assert len(calls) == 6
    publications = (await db_session.execute(
        select(DataPublication).where(
            DataPublication.dataset == DIVIDEND_HISTORY_DATASET
        )
    )).scalars().all()
    assert {publication.as_of_date for publication in publications} == {
        target,
        target + timedelta(days=1),
        target + timedelta(days=10),
    }
    assert (await db_session.execute(
        select(func.count(CorporateAction.id))
    )).scalar_one() == 3
    assert (await db_session.execute(
        select(Ticker).where(Ticker.ticker == "CCC.US")
    )).scalar_one().ticker == "CCC.US"


@pytest.mark.asyncio
async def test_factor_cross_section_is_quality_gated_and_published(db_session):
    as_of = date(2025, 12, 31)
    tickers = [f"T{index}.US" for index in range(10)]
    db_session.add_all([Ticker(ticker=ticker) for ticker in tickers])
    run = PipelineRun(pipeline_name="screener", target_date=as_of, status="published")
    db_session.add(run)
    await db_session.flush()
    db_session.add(DataPublication(dataset="screener", as_of_date=as_of, pipeline_run_id=run.id))
    db_session.add(DataPublication(dataset="price_history", as_of_date=as_of, pipeline_run_id=run.id))
    for index, ticker in enumerate(tickers):
        db_session.add(StockScreenerSnapshot(
            ticker=ticker,
            date=as_of,
            sector="Tech" if index < 5 else "Health",
            market_cap=1_000_000 + index,
            pe_ratio=10 + index,
            pb_ratio=1 + index / 10,
            roe=0.1 + index / 100,
            gross_margin=0.3 + index / 100,
            debt_to_equity=1 - index / 20,
            sales_growth_5yr=0.02 + index / 100,
            close=100,
        ))
        for day_index, price_date in enumerate(pd.bdate_range(end=as_of, periods=260)):
            price = 100 + day_index * (0.05 + index * 0.005)
            db_session.add(DailyPrice(
                ticker=ticker,
                date=price_date.date(),
                close=price,
                adjusted_close=price,
                volume=100_000,
            ))
    await db_session.commit()

    result = await compute_and_store_factors(db_session, as_of)
    assert result["version"] == FACTOR_VERSION
    factor_count = (await db_session.execute(
        select(func.count(FactorValue.id)).where(FactorValue.factor_name == "composite")
    )).scalar_one()
    assert factor_count == 10
    factor_publication = (await db_session.execute(
        select(DataPublication).where(DataPublication.dataset == "factors")
    )).scalar_one()
    assert factor_publication.as_of_date == as_of
    screener_publication = (await db_session.execute(
        select(DataPublication).where(DataPublication.dataset == "screener")
    )).scalar_one()
    stored_factor = (await db_session.execute(select(FactorValue).limit(1))).scalar_one()
    price_publication = (await db_session.execute(
        select(DataPublication).where(DataPublication.dataset == "price_history")
    )).scalar_one()
    assert stored_factor.available_at == max(
        screener_publication.published_at,
        price_publication.published_at,
    )


@pytest.mark.asyncio
async def test_factor_publication_requires_price_factor_coverage(db_session):
    as_of = date(2025, 12, 31)
    run = PipelineRun(pipeline_name="screener", target_date=as_of, status="published")
    db_session.add(run)
    await db_session.flush()
    db_session.add_all([
        DataPublication(dataset="screener", as_of_date=as_of, pipeline_run_id=run.id),
        DataPublication(dataset="price_history", as_of_date=as_of, pipeline_run_id=run.id),
    ])
    for index in range(10):
        db_session.add(StockScreenerSnapshot(
            ticker=f"T{index}.US",
            date=as_of,
            sector="Tech",
            pe_ratio=10 + index,
            pb_ratio=1 + index / 10,
            roe=0.1 + index / 100,
            gross_margin=0.3 + index / 100,
            debt_to_equity=1 - index / 20,
            sales_growth_5yr=0.02 + index / 100,
            close=100,
        ))
    await db_session.commit()

    with pytest.raises(ValueError, match="price_factors=0.00%"):
        await compute_and_store_factors(db_session, as_of)

    assert (await db_session.execute(select(func.count(FactorValue.id)))).scalar_one() == 0
    assert (await db_session.execute(
        select(DataPublication).where(DataPublication.dataset == "factors")
    )).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_factor_rows_roll_back_when_publication_fails(db_session, monkeypatch):
    as_of = date(2025, 12, 31)
    tickers = [f"T{index}.US" for index in range(10)]
    db_session.add_all([Ticker(ticker=ticker) for ticker in tickers])
    source_run = PipelineRun(pipeline_name="screener", target_date=as_of, status="published")
    db_session.add(source_run)
    await db_session.flush()
    db_session.add_all([
        DataPublication(dataset="screener", as_of_date=as_of, pipeline_run_id=source_run.id),
        DataPublication(dataset="price_history", as_of_date=as_of, pipeline_run_id=source_run.id),
    ])
    for index, ticker in enumerate(tickers):
        db_session.add(StockScreenerSnapshot(
            ticker=ticker,
            date=as_of,
            sector="Tech" if index < 5 else "Health",
            pe_ratio=10 + index,
            pb_ratio=1 + index / 10,
            roe=0.1 + index / 100,
            gross_margin=0.3 + index / 100,
            debt_to_equity=1 - index / 20,
            sales_growth_5yr=0.02 + index / 100,
            close=100,
        ))
        for day_index, price_date in enumerate(pd.bdate_range(end=as_of, periods=80)):
            db_session.add(DailyPrice(
                ticker=ticker,
                date=price_date.date(),
                close=100 + day_index + index,
                adjusted_close=100 + day_index + index,
            ))
    await db_session.commit()

    async def fail_publication(*args, **kwargs):
        raise RuntimeError("simulated publication failure")

    monkeypatch.setattr(
        "services.quant.factor_engine.publish_datasets_and_finish",
        fail_publication,
    )
    with pytest.raises(RuntimeError, match="simulated publication failure"):
        await compute_and_store_factors(db_session, as_of)

    assert (await db_session.execute(select(func.count(FactorValue.id)))).scalar_one() == 0
    assert (await db_session.execute(
        select(DataPublication).where(DataPublication.dataset == "factors")
    )).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_daily_screener_persists_adjusted_prices_and_bulk_actions(db_session, monkeypatch):
    target = date(2025, 1, 10)
    frame = pd.DataFrame([
        {
            "ticker": ticker,
            "code": ticker.split(".")[0],
            "date": target.isoformat(),
            "open": 49,
            "high": 51,
            "low": 48,
            "close": 50,
            "adjusted_close": 100 + index,
            "volume": 1_000,
            "Name": ticker,
            "Sector": "Tech",
            "Industry": "Software",
            "MarketCapitalization": 1_000_000,
            "PERatio": 20,
            "PriceToBook": 2,
            "ROE": 0.2,
            "DebtToEquity": 0.5,
            "GrossMargin": 0.4,
            "SalesGrowth5yr": 0.1,
        }
        for index, ticker in enumerate(["AAA.US", "BBB.US"])
    ])
    frame.attrs["target_tickers"] = ["AAA.US", "BBB.US", "MISSING.US"]
    frame.attrs["priced_tickers"] = ["AAA.US", "BBB.US"]
    frame.attrs["universe_coverage"] = 1.0
    frame.attrs["bulk_splits"] = [{
        "ticker": "AAA.US",
        "code": "AAA",
        "date": target.isoformat(),
        "split": "2/1",
    }]
    frame.attrs["bulk_dividends"] = [{
        "ticker": "BBB.US",
        "code": "BBB",
        "date": target.isoformat(),
        "dividend": "0.25",
        "currency": "USD",
    }]
    frame.attrs["benchmark_prices"] = [{
        "ticker": "SPY.US",
        "date": target.isoformat(),
        "open": 199,
        "high": 202,
        "low": 198,
        "close": 200,
        "adjusted_close": 200,
        "volume": 2_000,
    }]

    async def fake_bulk(*args, **kwargs):
        return frame.copy()

    monkeypatch.setattr("services.screener_sync.fetch_and_merge_bulk_data", fake_bulk)

    backfill_tickers = set()
    dividend_backfill_options = {}

    async def skip_dividend_backfill(tickers, *args, **kwargs):
        backfill_tickers.update(tickers)
        dividend_backfill_options.update(kwargs)
        return {"status": "skipped", "reason": "fixture"}

    monkeypatch.setattr(
        "services.screener_sync.backfill_dividend_history_once",
        skip_dividend_backfill,
    )
    price_history_tickers = set()

    async def skip_price_history(tickers, **kwargs):
        price_history_tickers.update(tickers)
        assert kwargs["target_date"] == target
        assert kwargs["include_corporate_actions"] is False
        return {"status": "skipped", "reason": "fixture"}

    monkeypatch.setattr(
        "services.screener_sync.backfill_price_history",
        skip_price_history,
    )

    async def stale_screener_publication(dataset):
        assert dataset == "screener"
        return target - timedelta(days=3)

    monkeypatch.setattr(
        "services.screener_sync.latest_published_date",
        stale_screener_publication,
    )

    async def partial_technicals(*args, **kwargs):
        return pd.DataFrame([{
            "ticker": "AAA.US",
            "performance_1d": 0.1,
        }])

    monkeypatch.setattr(
        "services.screener_sync.calculate_technicals_locally",
        partial_technicals,
    )

    result = await run_screener_pipeline(
        target.isoformat(),
        observe_current_universe=True,
    )
    await db_session.rollback()

    assert result["status"] == "published"
    assert backfill_tickers == {"AAA.US", "BBB.US"}
    assert dividend_backfill_options["required_through_date"] == target
    assert price_history_tickers == {"AAA.US", "BBB.US", "SPY.US"}
    prices = (await db_session.execute(
        select(DailyPrice).order_by(DailyPrice.ticker)
    )).scalars().all()
    assert [price.ticker for price in prices] == ["AAA.US", "BBB.US", "SPY.US"]
    assert [float(price.adjusted_close) for price in prices] == [100.0, 101.0, 200.0]
    snapshots = (await db_session.execute(
        select(StockScreenerSnapshot).order_by(StockScreenerSnapshot.ticker)
    )).scalars().all()
    assert float(snapshots[0].performance_1d) == pytest.approx(0.1)
    assert snapshots[1].performance_1d is None
    assert (await db_session.execute(select(func.count(CorporateAction.id)))).scalar_one() == 2
    publications = set((await db_session.execute(
        select(DataPublication.dataset).where(DataPublication.as_of_date == target)
    )).scalars())
    assert publications == {"screener", "price_history"}


@pytest.mark.asyncio
async def test_historical_screener_reconstruction_is_refused(db_session):
    with pytest.raises(ValueError, match="current fundamentals"):
        await run_screener_pipeline("2025-01-02")
    failed_run = (await db_session.execute(
        select(PipelineRun).where(PipelineRun.pipeline_name == "daily_screener")
    )).scalar_one()
    assert failed_run.status == "failed"


@pytest.mark.asyncio
async def test_cold_start_orchestration_includes_base_tickers(db_session, monkeypatch):
    from scripts import cold_start_init

    snapshot_date = date(2025, 1, 10)
    db_session.add(Ticker(ticker="AAA.US"))
    db_session.add(StockScreenerSnapshot(ticker="AAA.US", date=snapshot_date, close=10))
    await db_session.commit()
    captured = {}

    async def no_op(*args, **kwargs):
        return None

    async def fake_screener(*args, **kwargs):
        return {"status": "published", "as_of_date": snapshot_date.isoformat()}

    async def fake_backfill(tickers, **kwargs):
        captured["tickers"] = set(tickers)
        captured["target_date"] = kwargs["target_date"]
        captured["include_dividends"] = kwargs["include_dividends"]
        return {"status": "published"}

    async def fake_refresh(target):
        captured["refresh_date"] = target
        return 1

    async def fake_factor(db, target):
        captured["factor_date"] = target
        return {"version": FACTOR_VERSION}

    monkeypatch.setattr(cold_start_init, "init_db", no_op)
    monkeypatch.setattr(cold_start_init, "idempotent_seed_base_tickers", no_op)
    monkeypatch.setattr(cold_start_init, "run_screener_pipeline", fake_screener)
    monkeypatch.setattr(cold_start_init, "backfill_price_history", fake_backfill)
    monkeypatch.setattr(cold_start_init, "refresh_screener_technicals", fake_refresh)
    monkeypatch.setattr(cold_start_init, "compute_and_store_factors", fake_factor)

    await cold_start_init.cold_start()
    assert {"AAA.US", *cold_start_init.BASE_TICKERS}.issubset(captured["tickers"])
    assert captured["target_date"] == snapshot_date
    assert captured["include_dividends"] is False
    assert captured["refresh_date"] == snapshot_date
    assert captured["factor_date"] == snapshot_date


@pytest.mark.asyncio
async def test_backtest_persists_immutable_strategy_and_signal_snapshots(db_session):
    signal_date = date(2025, 1, 2)
    tickers = ["AAA.US", "BBB.US", "SPY.US"]
    db_session.add_all([Ticker(ticker=ticker) for ticker in tickers])
    db_session.add_all([
        UniverseMembership(
            universe="TEST",
            ticker=ticker,
            effective_from=date(2024, 1, 1),
        )
        for ticker in tickers[:2]
    ])
    factor_run = PipelineRun(
        pipeline_name="factor_cross_section",
        target_date=signal_date,
        status="published",
        version=FACTOR_VERSION,
    )
    db_session.add(factor_run)
    await db_session.flush()
    db_session.add(DataPublication(
        dataset="factors",
        as_of_date=signal_date,
        pipeline_run_id=factor_run.id,
    ))
    for index, ticker in enumerate(tickers[:2]):
        db_session.add(FactorValue(
            ticker=ticker,
            as_of_date=signal_date,
            factor_name="composite",
            raw_value=2 - index,
            normalized_value=2 - index,
            version=FACTOR_VERSION,
            source_run_id=factor_run.id,
            available_at=pd.Timestamp("2025-01-02 23:59").to_pydatetime(),
            details={"sector": "Tech" if index == 0 else "Health"},
        ))
    trading_dates = pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"])
    for ticker in tickers:
        for day_index, trading_date in enumerate(trading_dates):
            db_session.add(DailyPrice(
                ticker=ticker,
                date=trading_date.date(),
                close=100 + day_index,
                adjusted_close=100 + day_index,
                volume=1000,
            ))
    await db_session.commit()

    config = BacktestConfig(
        start_date=signal_date,
        end_date=date(2025, 1, 7),
        universe="TEST",
        rebalance_frequency="all",
        top_n=2,
        max_position_weight=1.0,
        max_sector_weight=1.0,
        transaction_cost_bps=0,
        slippage_bps=0,
    )
    run = await run_and_store_backtest(db_session, config, name="Snapshot Test")
    assert run.status == "completed"
    strategy = (await db_session.execute(select(StrategyDefinition))).scalar_one()
    assert strategy.version.startswith(f"{FACTOR_VERSION}-")
    snapshots = (await db_session.execute(
        select(SignalSnapshot).order_by(SignalSnapshot.rank)
    )).scalars().all()
    assert [snapshot.ticker for snapshot in snapshots] == ["AAA.US", "BBB.US"]
    assert sum(snapshot.target_weight for snapshot in snapshots) == pytest.approx(1.0)
    assert {snapshot.backtest_run_id for snapshot in snapshots} == {run.id}
    assert run.diagnostics["factor_source_run_ids"] == [factor_run.id]

    second_run = await run_and_store_backtest(db_session, config, name="Snapshot Test")
    all_snapshots = (await db_session.execute(
        select(SignalSnapshot).order_by(SignalSnapshot.backtest_run_id, SignalSnapshot.rank)
    )).scalars().all()
    assert len(all_snapshots) == 4
    assert {snapshot.backtest_run_id for snapshot in all_snapshots} == {run.id, second_run.id}


@pytest.mark.asyncio
async def test_strategy_definition_creation_is_concurrency_safe(db_session):
    from database import async_session_maker
    from services.quant.backtest import _get_or_create_strategy

    async def create_strategy():
        async with async_session_maker() as session:
            strategy = await _get_or_create_strategy(
                session,
                name="Concurrent Strategy",
                version="v1-test",
                config={"factor": "composite"},
            )
            await session.commit()
            return strategy.id

    strategy_ids = await asyncio.gather(create_strategy(), create_strategy())

    assert strategy_ids[0] == strategy_ids[1]
    count = (await db_session.execute(select(func.count(StrategyDefinition.id)))).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_stored_backtest_loads_latest_pre_start_factor_cross_section(db_session):
    pre_start = date(2024, 12, 31)
    start = date(2025, 1, 2)
    tickers = ["AAA.US", "BBB.US", "SPY.US"]
    db_session.add_all([Ticker(ticker=ticker) for ticker in tickers])
    db_session.add_all([
        UniverseMembership(
            universe="TEST",
            ticker=ticker,
            effective_from=date(2024, 1, 1),
        )
        for ticker in tickers[:2]
    ])
    factor_run = PipelineRun(
        pipeline_name="factor_cross_section",
        target_date=pre_start,
        status="published",
        version=FACTOR_VERSION,
    )
    db_session.add(factor_run)
    await db_session.flush()
    db_session.add(DataPublication(
        dataset="factors",
        as_of_date=pre_start,
        pipeline_run_id=factor_run.id,
    ))
    for index, ticker in enumerate(tickers[:2]):
        db_session.add(FactorValue(
            ticker=ticker,
            as_of_date=pre_start,
            factor_name="composite",
            raw_value=2 - index,
            normalized_value=2 - index,
            version=FACTOR_VERSION,
            source_run_id=factor_run.id,
            available_at=pd.Timestamp("2024-12-31 20:00").to_pydatetime(),
            details={"sector": "Tech" if index == 0 else "Health"},
        ))
    for ticker in tickers:
        for day_index, trading_date in enumerate(pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"])):
            db_session.add(DailyPrice(
                ticker=ticker,
                date=trading_date.date(),
                close=100 + day_index,
                adjusted_close=100 + day_index,
                volume=1000,
            ))
    await db_session.commit()

    run = await run_and_store_backtest(
        db_session,
        BacktestConfig(
            start_date=start,
            end_date=date(2025, 1, 6),
            universe="TEST",
            rebalance_frequency="monthly",
            top_n=2,
            max_position_weight=1.0,
            max_sector_weight=1.0,
            transaction_cost_bps=0,
            slippage_bps=0,
        ),
        name="Pre-start Signal Test",
    )

    assert run.status == "completed"
    assert run.diagnostics["pre_start_signal_date"] == "2024-12-31"
    assert run.diagnostics["rebalances"][0]["execution_date"] == "2025-01-02"
