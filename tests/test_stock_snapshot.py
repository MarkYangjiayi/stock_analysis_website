from datetime import date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from main import app
from models import (
    CorporateAction,
    DailyPrice,
    DataPublication,
    FinancialStatement,
    PipelineRun,
    StockScreenerSnapshot,
    Ticker,
    UniverseMembership,
)
from services.raw_store import persist_snapshot
from services.stock_snapshot import _performance_for_years, get_market_snapshot
from services.universe import LIVE_UNIVERSE_SOURCE


def test_long_term_performance_requires_history_and_uses_adjusted_prices():
    prices = [
        (date(2020, 1, 2), 50.0),
        (date(2023, 1, 2), 80.0),
        (date(2026, 1, 2), 100.0),
    ]
    assert _performance_for_years(prices, 3) == pytest.approx(0.25)
    assert _performance_for_years(prices, 10) is None


async def _seed_complete_snapshot(db_session):
    start = date(2025, 4, 26)
    price_dates = [start + timedelta(days=index) for index in range(252)]
    as_of = price_dates[-1]
    latest_price = 100 + 251 / 10

    db_session.add(Ticker(
        ticker="AAA.US",
        name="AAA Corp",
        exchange="NYSE",
        sector="Technology",
        industry="Software",
        currency="USD",
    ))
    run = PipelineRun(
        pipeline_name="screener",
        target_date=as_of,
        status="published",
        stage="published",
        version="v1",
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(DataPublication(
        dataset="screener",
        as_of_date=as_of,
        pipeline_run_id=run.id,
        status="published",
    ))
    db_session.add(StockScreenerSnapshot(
        ticker="AAA.US",
        date=as_of,
        name="AAA Corp",
        exchange="NYSE",
        sector="Technology",
        industry="Software",
        country="USA",
        market_cap=1_000,
        pe_ratio=20,
        forward_pe=18,
        ps_ratio=5,
        pb_ratio=4,
        price_cash=3,
        price_fcf=25,
        ev_sales=5.2,
        ev_ebitda=30,
        shares_outstanding=10,
        shares_float=8,
        short_float=0.05,
        dividend_yield=0.01,
        dividend_growth_3yr=0.08,
        dividend_growth_5yr=0.07,
        current_ratio=2,
        quick_ratio=1.5,
        debt_to_equity=0.5,
        lt_debt_to_equity=0.4,
        gross_margin=0.6,
        operating_margin=0.2,
        net_profit_margin=0.15,
        roe=0.25,
        roa=0.12,
        roic=0.18,
        sales_growth_ttm=0.2,
        sales_growth_3yr=0.18,
        sales_growth_5yr=0.15,
        eps_growth_ttm=0.22,
        eps_growth_3yr=0.19,
        eps_growth_5yr=0.16,
        close=latest_price,
        volume=2_000,
        average_volume_3m=1_500,
        relative_volume=4 / 3,
        ma20=120,
        ma50=115,
        ma200=100,
        rsi_14=62,
        atr_14=3.5,
        beta_1yr=1.2,
        high_52w_rel=-0.02,
        low_52w_rel=0.4,
        performance_1d=0.01,
        performance_1w=0.02,
        performance_1m=0.03,
        performance_3m=0.04,
        performance_6m=0.05,
        performance_ytd=0.06,
        performance_1yr=0.07,
        target_price=140,
        analyst_recommendation=1.8,
        ipo_date=date(2020, 1, 1),
    ))
    db_session.add(UniverseMembership(
        universe="SP500",
        ticker="AAA.US",
        effective_from=as_of - timedelta(days=100),
        source=LIVE_UNIVERSE_SOURCE,
    ))

    for index, observed in enumerate(price_dates):
        close = 100 + index / 10
        db_session.add(DailyPrice(
            ticker="AAA.US",
            date=observed,
            open=close,
            high=close + 1,
            low=close - 1,
            close=close,
            adjusted_close=close,
            volume=1_000 + index,
        ))

    quarterly_dates = (
        date(2025, 12, 31),
        date(2025, 9, 30),
        date(2025, 6, 30),
        date(2025, 3, 31),
    )
    for fiscal_date in quarterly_dates:
        db_session.add(FinancialStatement(
            ticker="AAA.US",
            fiscal_date=fiscal_date,
            period="Quarterly",
            revenue=100,
            net_income=10,
            income_statement={
                "totalRevenue": 100,
                "netIncome": 10,
                "grossProfit": 60,
                "operatingIncome": 20,
            },
            balance_sheet={
                "cashAndShortTermInvestments": 100,
                "shortLongTermDebtTotal": 50,
                "totalStockholderEquity": 200,
                "commonStockSharesOutstanding": 10,
            },
            cash_flow={"freeCashFlow": 8},
        ))
    db_session.add_all([
        CorporateAction(
            ticker="AAA.US",
            ex_date=as_of - timedelta(days=30),
            action_type="dividend",
            cash_amount=0.25,
            currency="USD",
            source_id="d1",
        ),
        CorporateAction(
            ticker="AAA.US",
            ex_date=as_of - timedelta(days=180),
            action_type="dividend",
            cash_amount=0.25,
            currency="USD",
            source_id="d2",
        ),
    ])
    await persist_snapshot(
        db_session,
        "EODHD",
        "fundamentals",
        {
            "Highlights": {"EarningsShare": 2.5, "DividendShare": 1.0},
            "SplitsDividends": {"ExDividendDate": as_of.isoformat()},
            "Earnings": {
                "Trend": {
                    "0q": {"date": "2027-03-31", "period": "0q", "epsTrendCurrent": 0.8},
                    "+1y": {"date": "2027-12-31", "period": "+1y", "epsTrendCurrent": 4.0},
                }
            },
        },
        as_of_date=as_of,
        details={"ticker": "AAA.US"},
    )
    await db_session.commit()
    return as_of, latest_price


@pytest.mark.asyncio
async def test_market_snapshot_aggregates_local_sources(db_session):
    as_of, latest_price = await _seed_complete_snapshot(db_session)

    result = await get_market_snapshot("AAA", db_session)

    assert result["ticker"] == "AAA.US"
    assert result["source_dates"]["screener"] == as_of
    assert result["metrics"]["index_membership"]["value"] == ["S&P 500"]
    assert result["metrics"]["sales_ttm"]["value"] == pytest.approx(400)
    assert result["metrics"]["net_income_ttm"]["value"] == pytest.approx(40)
    assert result["metrics"]["enterprise_value"]["value"] == pytest.approx(950)
    assert result["metrics"]["book_per_share"]["value"] == pytest.approx(20)
    assert result["metrics"]["cash_per_share"]["value"] == pytest.approx(10)
    assert result["metrics"]["dividend_ttm"]["value"] == pytest.approx(0.5)
    assert result["metrics"]["sma20_distance"]["value"] == pytest.approx(latest_price / 120 - 1)
    assert result["metrics"]["high_52w"]["value"] == pytest.approx(latest_price + 1)
    assert result["metrics"]["high_52w"]["secondary_value"] == pytest.approx(latest_price / (latest_price + 1) - 1)
    assert result["metrics"]["change"]["value"] == pytest.approx(latest_price / (latest_price - 0.1) - 1)
    assert result["metrics"]["change"]["source_date"] == as_of
    assert result["metrics"]["eps_next_quarter"]["value"] == pytest.approx(0.8)
    assert result["metrics"]["eps_growth_this_year"]["unavailable_reason"] == "The latest published Screener snapshot does not contain this value."
    assert result["coverage"]["available"] > 40

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/stocks/AAA/market-snapshot")
    assert response.status_code == 200
    assert response.json()["metrics"]["forward_pe"]["value"] == pytest.approx(18)


@pytest.mark.asyncio
async def test_market_snapshot_normalizes_legacy_screener_values(db_session):
    await _seed_complete_snapshot(db_session)
    snapshot = (
        await db_session.execute(
            select(StockScreenerSnapshot).where(StockScreenerSnapshot.ticker == "AAA.US")
        )
    ).scalar_one()
    snapshot.pe_ratio = 0
    snapshot.quick_ratio = -1
    snapshot.ps_ratio = 999_999.9999
    snapshot.target_price = 0
    snapshot.rsi_14 = 101
    await db_session.commit()

    result = await get_market_snapshot("AAA", db_session)

    for key in ("pe_ratio", "quick_ratio", "ps_ratio", "target_price", "rsi_14"):
        assert result["metrics"][key]["value"] is None


@pytest.mark.asyncio
async def test_market_snapshot_endpoint_is_non_blocking_when_sources_are_missing(db_session):
    db_session.add(Ticker(ticker="EMPTY.US", name="Empty", currency="USD"))
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/stocks/EMPTY/market-snapshot")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ticker"] == "EMPTY.US"
    assert payload["coverage"]["available"] == 0
    assert payload["metrics"]["market_cap"]["value"] is None
    assert payload["metrics"]["market_cap"]["unavailable_reason"]
