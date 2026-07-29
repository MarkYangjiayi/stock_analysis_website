from datetime import date, timedelta

import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from models import (
    CorporateAction,
    DataPublication,
    PipelineRun,
    StockScreenerSnapshot,
    UniverseMembership,
)
from services.screener_fields import SUPPORTED_FINVIZ_FIELDS
from services.screener_metrics import (
    calculate_dividend_growth,
    calculate_price_metrics,
    classify_candlestick,
    extract_fundamental_metrics,
)
from services.screener_query import get_screener_metadata, query_screener
from services.screener_sync import refresh_screener_technicals


def test_fundamental_extractor_uses_provider_fields_and_safe_fallbacks():
    payload = {
        "General": {
            "Exchange": "NASDAQ",
            "CountryName": "USA",
            "IPODate": "1980-12-12",
        },
        "Highlights": {
            "MarketCapitalization": 1_000,
            "PERatio": 10,
            "RevenueTTM": 500,
            "GrossProfitTTM": 200,
            "OperatingMarginTTM": 0.25,
            "ProfitMargin": 0.20,
            "ReturnOnAssetsTTM": 0.12,
            "ReturnOnEquityTTM": 0.30,
            "QuarterlyEarningsGrowthYOY": 0.15,
            "QuarterlyRevenueGrowthYOY": 0.10,
        },
        "Valuation": {
            "ForwardPE": 9,
            "PriceBookMRQ": 4,
            "PriceSalesTTM": 2,
            "EnterpriseValueEbitda": 8,
            "EnterpriseValueRevenue": 1.8,
        },
        "SharesStats": {
            "SharesOutstanding": 100,
            "SharesFloat": 80,
            "ShortPercentFloat": 0.05,
            "PercentInsiders": 10,
            "PercentInstitutions": 70,
        },
        "SplitsDividends": {"PayoutRatio": 0.30, "ForwardAnnualDividendYield": 0.02},
        "AnalystRatings": {"Rating": 1.8, "TargetPrice": 15},
        "Earnings": {
            "History": {
                f"2025-{month:02d}-01": {"epsActual": 2 if month >= 5 else 1}
                for month in range(1, 9)
            },
        },
        "Financials": {
            "Balance_Sheet": {
                "quarterly": {
                    "2025-12-31": {
                        "cashAndShortTermInvestments": 100,
                        "totalCurrentAssets": 300,
                        "totalCurrentLiabilities": 150,
                        "inventory": 25,
                        "totalStockholderEquity": 400,
                        "longTermDebtTotal": 80,
                        "shortLongTermDebtTotal": 100,
                        "netInvestedCapital": 500,
                    }
                }
            },
            "Income_Statement": {
                "quarterly": {
                    f"2025-{quarter:02d}-01": {
                        "totalRevenue": 125,
                        "grossProfit": 50,
                        "operatingIncome": 30,
                        "netIncome": 25,
                        "ebit": 30,
                        "incomeTaxExpense": 6,
                        "incomeBeforeTax": 30,
                    }
                    for quarter in range(1, 9)
                },
                "yearly": {
                    f"{year}-12-31": {"totalRevenue": 100 * (year - 2018)}
                    for year in range(2019, 2026)
                },
            },
            "Cash_Flow": {
                "quarterly": {
                    f"2025-{quarter:02d}-01": {"freeCashFlow": 10}
                    for quarter in range(1, 5)
                }
            },
        },
    }
    metrics = extract_fundamental_metrics(payload)
    assert metrics["exchange"] == "NASDAQ"
    assert metrics["ipo_date"] == date(1980, 12, 12)
    assert metrics["price_cash"] == pytest.approx(10)
    assert metrics["price_fcf"] == pytest.approx(25)
    assert metrics["gross_margin"] == pytest.approx(0.4)
    assert metrics["current_ratio"] == pytest.approx(2)
    assert metrics["quick_ratio"] == pytest.approx(275 / 150)
    assert metrics["short_float"] == pytest.approx(0.05)
    assert metrics["insider_ownership"] == pytest.approx(0.10)
    assert metrics["institutional_ownership"] == pytest.approx(0.70)
    assert metrics["eps_growth_ttm"] == pytest.approx(1)
    assert metrics["roic"] == pytest.approx(0.192)


def test_fundamental_extractor_preserves_zero_and_does_not_invent_formula_inputs():
    metrics = extract_fundamental_metrics({
        "Highlights": {
            "MarketCapitalization": 100,
            "OperatingMarginTTM": 0,
            "QuarterlyRevenueGrowthYOY": 0,
        },
        "Valuation": {"TrailingPE": 0},
        "Financials": {
            "Balance_Sheet": {
                "quarterly": {
                    "2025-12-31": {
                        "cashAndShortTermInvestments": 0,
                        "totalCurrentAssets": 50,
                        "totalCurrentLiabilities": 25,
                    },
                },
            },
        },
    })
    assert metrics["pe_ratio"] == 0
    assert metrics["operating_margin"] == 0
    assert metrics["sales_growth_qoq"] == 0
    assert metrics["price_cash"] is None
    assert metrics["quick_ratio"] is None
    assert metrics["roic"] is None

    net_debt_only = extract_fundamental_metrics({
        "Financials": {
            "Balance_Sheet": {
                "quarterly": {
                    "2025-12-31": {
                        "netDebt": -50,
                        "totalStockholderEquity": 100,
                    },
                },
            },
        },
    })
    assert net_debt_only["debt_to_equity"] is None


def test_fundamental_extractor_uses_semantic_units_and_complete_formula_windows():
    metrics = extract_fundamental_metrics({
        "Highlights": {
            "MarketCapitalization": 800,
            "ReturnOnEquityTTM": 2.5,
            "TotalDebt": 50,
        },
        "SharesStats": {
            "PercentInsiders": 63.091,
            "PercentInstitutions": 8.134,
            "ShortPercentFloat": 0.0001,
        },
        "Financials": {
            "Balance_Sheet": {
                "quarterly": {
                    "2025-12-31": {
                        "totalStockholderEquity": 100,
                        "netInvestedCapital": 200,
                    },
                },
            },
            "Income_Statement": {
                "yearly": {
                    "2025-12-31": {
                        "ebit": 40,
                        "incomeBeforeTax": 40,
                    },
                },
            },
            "Cash_Flow": {
                "quarterly": {
                    "2025-12-31": {"freeCashFlow": 10},
                },
                "yearly": {
                    "2025-12-31": {"freeCashFlow": 80},
                },
            },
        },
        "Earnings": {
            "Annual": {
                "2025-12-31": {"epsActual": 99},
                "2024-12-31": {"epsActual": 1},
            },
            "History": {
                f"2025-{month:02d}-01": {"epsActual": month}
                for month in range(1, 8)
            },
        },
    })
    assert metrics["roe"] == pytest.approx(2.5)
    assert metrics["insider_ownership"] == pytest.approx(0.63091)
    assert metrics["institutional_ownership"] == pytest.approx(0.08134)
    assert metrics["short_float"] == pytest.approx(0.0001)
    assert metrics["debt_to_equity"] == pytest.approx(0.5)
    assert metrics["fcf"] == pytest.approx(80)
    assert metrics["price_fcf"] == pytest.approx(10)
    assert metrics["roic"] is None
    assert metrics["eps_growth_ttm"] is None


@pytest.mark.parametrize(
    ("candles", "expected"),
    [
        ([(10, 11, 9, 10.1)], "Doji"),
        ([(10, 10.6, 8.5, 10.5)], "Hammer"),
        ([(10, 12, 9.9, 10.5)], "Inverted Hammer"),
        ([(11, 11.2, 9.8, 10), (9.8, 11.5, 9.5, 11.2)], "Bullish Engulfing"),
        ([(10, 11.2, 9.8, 11), (11.2, 11.5, 9.5, 9.8)], "Bearish Engulfing"),
        ([(12, 12.2, 9.8, 10), (9.5, 9.8, 9.3, 9.6), (9.7, 11.7, 9.5, 11.5)], "Morning Star"),
        ([(10, 12.2, 9.8, 12), (12.2, 12.4, 11.9, 12.1), (12, 12.2, 10.3, 10.5)], "Evening Star"),
        ([(10, 12.05, 9.95, 12)], "Bullish Marubozu"),
        ([(12, 12.05, 9.95, 10)], "Bearish Marubozu"),
    ],
)
def test_supported_candlestick_patterns(candles, expected):
    rows = pd.DataFrame(candles, columns=["open_adj", "high_adj", "low_adj", "close_adj"])
    assert classify_candlestick(rows) == expected


def test_price_metrics_are_adjusted_and_cover_primary_technicals():
    dates = pd.bdate_range("2025-01-01", periods=260)
    rows = pd.DataFrame({
        "date": [value.date() for value in dates],
        "open": [100 + index for index in range(260)],
        "high": [102 + index for index in range(260)],
        "low": [99 + index for index in range(260)],
        "close": [101 + index for index in range(260)],
        "adjusted_close": [50.5 + index / 2 for index in range(260)],
        "volume": [100_000 + index for index in range(260)],
    })
    metrics = calculate_price_metrics(rows)
    assert metrics["ma200"] is not None
    assert metrics["performance_1yr"] is not None
    assert metrics["volatility_1m"] is not None
    assert metrics["atr_14"] is not None
    assert metrics["high_52w_rel"] <= 0
    assert metrics["low_52w_rel"] >= 0


def test_ytd_performance_uses_the_prior_year_close():
    rows = pd.DataFrame({
        "date": [date(2024, 12, 31), date(2025, 1, 2)],
        "open": [100, 109],
        "high": [101, 111],
        "low": [99, 108],
        "close": [100, 110],
        "adjusted_close": [100, 110],
        "volume": [1_000, 1_100],
    })
    assert calculate_price_metrics(rows)["performance_ytd"] == pytest.approx(0.10)


def test_dividend_growth_uses_complete_annual_buckets():
    actions = [
        (date(year, 3, 1), amount)
        for year, amount in ((2020, 1), (2021, 1.1), (2022, 1.2), (2023, 1.3), (2024, 1.4), (2025, 1.5))
    ]
    metrics = calculate_dividend_growth(actions, date(2025, 12, 31))
    assert metrics["dividend_growth_1yr"] == pytest.approx(1.5 / 1.4 - 1)
    assert metrics["dividend_growth_3yr"] == pytest.approx((1.5 / 1.2) ** (1 / 3) - 1)
    assert metrics["dividend_growth_5yr"] == pytest.approx((1.5 / 1) ** (1 / 5) - 1)


def test_dividend_growth_ignores_an_incomplete_december():
    actions = [
        (date(2023, 12, 1), 1.0),
        (date(2024, 12, 1), 1.2),
        (date(2025, 12, 1), 0.3),
        (date(2025, 12, 20), 0.9),
    ]
    metrics = calculate_dividend_growth(actions, date(2025, 12, 15))
    assert metrics["dividend_growth_1yr"] == pytest.approx(0.20)


@pytest.mark.asyncio
async def test_metadata_and_generic_query_are_allowlisted_and_point_in_time(db_session):
    as_of = date.today() - timedelta(days=1)
    run = PipelineRun(
        pipeline_name="daily_screener",
        target_date=as_of,
        status="published",
        stage="published",
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(DataPublication(
        dataset="screener",
        as_of_date=as_of,
        pipeline_run_id=run.id,
        status="published",
    ))
    db_session.add_all([
        StockScreenerSnapshot(
            ticker="AAA.US",
            name="Alpha",
            date=as_of,
            sector="Technology",
            market_cap=10_000,
            pe_ratio=10,
            roe=0.20,
            ipo_date=date(2020, 1, 15),
            close=100,
            ma50=90,
            volume=1000,
        ),
        StockScreenerSnapshot(
            ticker="BBB.US",
            name="Beta",
            date=as_of,
            sector="Healthcare",
            market_cap=5_000,
            pe_ratio=30,
            roe=0.05,
            ipo_date=date(2024, 6, 1),
            close=50,
            ma50=60,
            volume=500,
        ),
        UniverseMembership(
            universe="SP500",
            ticker="AAA.US",
            effective_from=as_of,
            source_run_id=run.id,
        ),
        UniverseMembership(
            universe="RUSSELL2000",
            ticker="NOT-IN-SNAPSHOT.US",
            effective_from=as_of,
            source_run_id=run.id,
        ),
    ])
    await db_session.commit()

    metadata = await get_screener_metadata(db_session)
    assert metadata["supported_finviz_fields"] == 66
    assert metadata["record_count"] == 2
    assert any(field["id"] == "pe_ratio" and field["available"] for field in metadata["fields"])
    index_metadata = next(field for field in metadata["fields"] if field["id"] == "index")
    assert index_metadata["result_column"] is False
    assert index_metadata["coverage"] == pytest.approx(0.5)
    assert index_metadata["options"] == [{"value": "SP500", "label": "S&P 500"}]

    result = await query_screener({
        "filters": [
            {"field": "pe_ratio", "operator": "between", "value": [5, 20]},
            {"field": "index", "operator": "eq", "value": "SP500"},
        ],
        "sort": {"field": "roe", "direction": "desc"},
        "columns": ["sector", "pe_ratio", "roe"],
        "limit": 50,
        "offset": 0,
    }, db_session)
    assert result["total"] == 1
    assert result["items"] == [{
        "ticker": "AAA.US",
        "name": "Alpha",
        "sector": "Technology",
        "pe_ratio": 10.0,
        "roe": 0.2,
    }]

    ipo_result = await query_screener({
        "filters": [{
            "field": "ipo_date",
            "operator": "between",
            "value": ["2019-01-01", "2021-12-31"],
        }],
        "columns": ["ipo_date"],
    }, db_session)
    assert ipo_result["total"] == 1
    assert ipo_result["items"][0]["ipo_date"] == "2020-01-15"

    above_ma_result = await query_screener({
        "filters": [{"field": "price_vs_ma50", "operator": "eq", "value": "above"}],
        "columns": ["close", "ma50"],
    }, db_session)
    assert above_ma_result["total"] == 1
    assert above_ma_result["items"][0]["ticker"] == "AAA.US"

    with pytest.raises(ValueError, match="unsupported filter"):
        await query_screener({
            "filters": [{"field": "drop_table", "operator": "eq", "value": 1}],
        }, db_session)

    from main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        metadata_response = await client.get("/api/stocks/screener/metadata")
        assert metadata_response.status_code == 200
        assert metadata_response.json()["supported_finviz_fields"] == 66

        query_response = await client.post("/api/stocks/screener/query", json={
            "filters": [{"field": "sector", "operator": "in", "value": ["Technology"]}],
            "sort": {"field": "market_cap", "direction": "desc"},
            "columns": ["sector", "market_cap"],
            "limit": 10,
            "offset": 0,
        })
        assert query_response.status_code == 200
        assert query_response.json()["items"][0]["ticker"] == "AAA.US"

        invalid_response = await client.post("/api/stocks/screener/query", json={
            "filters": [{"field": "drop_table", "operator": "eq", "value": 1}],
        })
        assert invalid_response.status_code == 422

        invalid_operand_response = await client.post("/api/stocks/screener/query", json={
            "filters": [{"field": "pe_ratio", "operator": "gte", "value": None}],
        })
        assert invalid_operand_response.status_code == 422

        invalid_date_operand_response = await client.post("/api/stocks/screener/query", json={
            "filters": [{"field": "ipo_date", "operator": "eq", "value": ["2020-01-15"]}],
        })
        assert invalid_date_operand_response.status_code == 422


@pytest.mark.asyncio
async def test_index_metadata_stays_disabled_until_separate_memberships_exist(db_session):
    as_of = date.today() - timedelta(days=1)
    run = PipelineRun(
        pipeline_name="daily_screener",
        target_date=as_of,
        status="published",
        stage="published",
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add_all([
        DataPublication(
            dataset="screener",
            as_of_date=as_of,
            pipeline_run_id=run.id,
            status="published",
        ),
        StockScreenerSnapshot(
            ticker="AAA.US",
            name="Alpha",
            date=as_of,
            close=100,
            volume=1_000,
        ),
        UniverseMembership(
            universe="SP500_RUSSELL2000",
            ticker="AAA.US",
            effective_from=as_of,
            source_run_id=run.id,
        ),
    ])
    await db_session.commit()

    metadata = await get_screener_metadata(db_session)
    index_field = next(field for field in metadata["fields"] if field["id"] == "index")
    assert index_field["available"] is False
    assert index_field["coverage"] == 0
    assert index_field["options"] == []


@pytest.mark.asyncio
async def test_cold_start_refresh_populates_dividend_growth_without_price_rows(db_session, monkeypatch):
    snapshot_date = date(2025, 12, 31)
    db_session.add(StockScreenerSnapshot(
        ticker="AAA.US",
        name="Alpha",
        date=snapshot_date,
        close=100,
        volume=1_000,
    ))
    db_session.add_all([
        CorporateAction(
            ticker="AAA.US",
            ex_date=date(2024, 3, 1),
            action_type="dividend",
            cash_amount=1.0,
            source_id="2024",
        ),
        CorporateAction(
            ticker="AAA.US",
            ex_date=date(2025, 3, 1),
            action_type="dividend",
            cash_amount=1.2,
            source_id="2025",
        ),
    ])
    await db_session.commit()

    async def no_technicals(*args, **kwargs):
        return pd.DataFrame()

    monkeypatch.setattr("services.screener_sync.calculate_technicals_locally", no_technicals)
    assert await refresh_screener_technicals(snapshot_date) == 1

    await db_session.rollback()
    refreshed = (await db_session.execute(
        select(StockScreenerSnapshot).where(
            StockScreenerSnapshot.ticker == "AAA.US",
            StockScreenerSnapshot.date == snapshot_date,
        )
    )).scalar_one()
    assert float(refreshed.dividend_growth_1yr) == pytest.approx(0.2)
