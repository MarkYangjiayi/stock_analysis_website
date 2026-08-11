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
            "Highlights": {
                "MarketCapitalization": 9_999,
                "EarningsShare": 2.5,
                "DividendShare": 1.0,
            },
            "Valuation": {"EnterpriseValue": 950},
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
    assert result["metrics"]["market_cap"]["value"] == pytest.approx(1_000)
    assert result["metrics"]["market_cap"]["source_date"] == as_of
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
    assert "local provider fundamentals" in result["metrics"]["eps_growth_this_year"]["unavailable_reason"]
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
    as_of, latest_price = await _seed_complete_snapshot(db_session)
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
    snapshot.ma20 = None
    await db_session.commit()

    result = await get_market_snapshot("AAA", db_session)

    for key in ("pe_ratio", "quick_ratio", "ps_ratio", "target_price"):
        assert result["metrics"][key]["value"] is None
    assert result["metrics"]["rsi_14"]["value"] == pytest.approx(100)
    local_ma20 = sum(100 + index / 10 for index in range(232, 252)) / 20
    assert result["metrics"]["sma20_distance"]["value"] == pytest.approx(
        latest_price / local_ma20 - 1
    )
    assert result["metrics"]["sma20_distance"]["source_date"] == as_of


@pytest.mark.asyncio
async def test_market_snapshot_fills_off_universe_ticker_from_local_sources(db_session):
    as_of = date(2026, 1, 2)
    run = PipelineRun(
        pipeline_name="screener",
        target_date=as_of,
        status="published",
        stage="published",
        version="v1",
    )
    db_session.add_all([
        Ticker(
            ticker="MELI.US",
            name="MercadoLibre",
            exchange="NASDAQ",
            sector="Consumer Cyclical",
            industry="Internet Retail",
            currency="USD",
        ),
        Ticker(ticker="SPY.US", name="SPDR S&P 500 ETF", currency="USD"),
        run,
    ])
    await db_session.flush()
    db_session.add(DataPublication(
        dataset="screener",
        as_of_date=as_of,
        pipeline_run_id=run.id,
        status="published",
    ))
    for index in range(20):
        db_session.add(StockScreenerSnapshot(
            ticker=f"PEER{index}.US",
            name=f"Peer {index}",
            date=as_of,
            exchange="NASDAQ",
            sector="Consumer Cyclical",
            industry="Internet Retail",
            pe_ratio=10 + index,
            close=100,
            volume=1_000,
        ))

    start = as_of - timedelta(days=259)
    for index in range(260):
        observed = start + timedelta(days=index)
        close = 1_500 + index
        db_session.add_all([
            DailyPrice(
                ticker="MELI.US",
                date=observed,
                open=close - 1,
                high=close + 2,
                low=close - 2,
                close=close,
                adjusted_close=close,
                volume=1_000_000 + index,
            ),
            DailyPrice(
                ticker="SPY.US",
                date=observed,
                open=500 + index / 10,
                high=501 + index / 10,
                low=499 + index / 10,
                close=500 + index / 10,
                adjusted_close=500 + index / 10,
                volume=10_000_000,
            ),
        ])

    for fiscal_date in (
        date(2025, 12, 31),
        date(2025, 9, 30),
        date(2025, 6, 30),
        date(2025, 3, 31),
    ):
        db_session.add(FinancialStatement(
            ticker="MELI.US",
            fiscal_date=fiscal_date,
            period="Quarterly",
            revenue=5_000,
            net_income=500,
            income_statement={
                "totalRevenue": 5_000,
                "grossProfit": 2_500,
                "operatingIncome": 800,
                "netIncome": 500,
            },
            balance_sheet={
                "cashAndShortTermInvestments": 8_000,
                "shortLongTermDebtTotal": 4_000,
                "totalStockholderEquity": 20_000,
                "commonStockSharesOutstanding": 50,
            },
            cash_flow={"freeCashFlow": 400},
        ))

    await persist_snapshot(
        db_session,
        "EODHD",
        "fundamentals",
        {
            "General": {
                "Exchange": "NASDAQ",
                "CountryName": "Uruguay",
                "IPODate": "2007-08-10",
            },
            "Highlights": {
                "MarketCapitalization": 100_000,
                "PERatio": 20,
                "PEGRatio": 1.5,
                "RevenueTTM": 20_000,
                "GrossProfitTTM": 10_000,
                "OperatingMarginTTM": 0.16,
                "ProfitMargin": 0.10,
                "ReturnOnAssetsTTM": 0.12,
                "ReturnOnEquityTTM": 0.25,
                "EarningsShare": 50,
                "QuarterlyRevenueGrowthYOY": 0.30,
            },
            "Valuation": {
                "EnterpriseValue": 104_000,
                "ForwardPE": 18,
                "PriceBookMRQ": 5,
                "PriceSalesTTM": 5,
                "EnterpriseValueEbitda": 15,
                "EnterpriseValueRevenue": 4.8,
            },
            "SharesStats": {
                "SharesOutstanding": 50,
                "SharesFloat": 45,
                "ShortPercentFloat": 0.02,
                "PercentInsiders": 8,
                "PercentInstitutions": 75,
            },
            "AnalystRatings": {"Rating": 1.7, "TargetPrice": 2_100},
            "Financials": {
                "Balance_Sheet": {"quarterly": {"2025-12-31": {
                    "cashAndShortTermInvestments": 8_000,
                    "totalCurrentAssets": 12_000,
                    "totalCurrentLiabilities": 6_000,
                    "inventory": 1_000,
                    "totalStockholderEquity": 20_000,
                    "longTermDebtTotal": 3_000,
                    "shortLongTermDebtTotal": 4_000,
                    "netInvestedCapital": 25_000,
                }}},
                "Income_Statement": {"quarterly": {
                    fiscal_date.isoformat(): {
                        "totalRevenue": 5_000,
                        "grossProfit": 2_500,
                        "operatingIncome": 800,
                        "netIncome": 500,
                        "ebit": 800,
                        "incomeTaxExpense": 200,
                        "incomeBeforeTax": 1_000,
                    }
                    for fiscal_date in (
                        date(2025, 12, 31),
                        date(2025, 9, 30),
                        date(2025, 6, 30),
                        date(2025, 3, 31),
                    )
                }},
                "Cash_Flow": {"quarterly": {
                    fiscal_date.isoformat(): {"freeCashFlow": 400}
                    for fiscal_date in (
                        date(2025, 12, 31),
                        date(2025, 9, 30),
                        date(2025, 6, 30),
                        date(2025, 3, 31),
                    )
                }},
            },
        },
        as_of_date=as_of,
        details={"ticker": "MELI.US"},
    )
    await db_session.commit()

    result = await get_market_snapshot("MELI", db_session)

    assert result["metrics"]["index_membership"]["value"] is None
    assert result["metrics"]["market_cap"]["value"] == pytest.approx(100_000)
    assert result["metrics"]["market_cap"]["source_date"] == result["source_dates"]["provider"]
    assert result["metrics"]["forward_pe"]["value"] == pytest.approx(18)
    assert result["metrics"]["enterprise_value"]["value"] == pytest.approx(104_000)
    assert result["metrics"]["operating_margin"]["value"] == pytest.approx(0.16)
    assert result["metrics"]["shares_float"]["value"] == 45
    assert result["metrics"]["sales_ttm"]["value"] == pytest.approx(20_000)
    assert result["metrics"]["performance_1m"]["value"] is not None
    assert result["metrics"]["rsi_14"]["value"] is not None
    assert result["metrics"]["pe_ratio"]["percentile_scope"] == "industry"
    assert result["coverage"]["available"] > 35


@pytest.mark.asyncio
async def test_market_snapshot_does_not_mix_unknown_statement_currency_into_ev(db_session):
    as_of = date(2026, 1, 2)
    db_session.add(Ticker(
        ticker="FOREIGN.US",
        name="Foreign ADR",
        currency="USD",
    ))
    db_session.add(FinancialStatement(
        ticker="FOREIGN.US",
        fiscal_date=date(2025, 12, 31),
        period="Quarterly",
        balance_sheet={
            "cashAndShortTermInvestments": 1_000_000,
            "shortLongTermDebtTotal": 2_000_000,
            "totalStockholderEquity": 3_000_000,
        },
    ))
    await persist_snapshot(
        db_session,
        "EODHD",
        "fundamentals",
        {"Highlights": {"MarketCapitalization": 100_000}},
        as_of_date=as_of,
        details={"ticker": "FOREIGN.US"},
    )
    await db_session.commit()

    result = await get_market_snapshot("FOREIGN", db_session)

    assert result["metrics"]["market_cap"]["value"] == pytest.approx(100_000)
    assert result["metrics"]["enterprise_value"]["value"] is None
    assert "reporting currency" in result["metrics"]["enterprise_value"]["unavailable_reason"]


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
