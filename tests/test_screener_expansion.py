from datetime import date, timedelta

import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select

from models import (
    CorporateAction,
    DailyPrice,
    DataPublication,
    PipelineRun,
    StockScreenerSnapshot,
    Ticker,
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
from services.screener_sync import (
    _validate_index_components,
    calculate_technicals_locally,
    refresh_screener_technicals,
)
from services.universe import (
    HISTORICAL_UNIVERSE_SOURCE,
    LIVE_UNIVERSE_SOURCE,
    SCREENER_INDEXES,
    SCREENER_UNIVERSE,
)


def test_nasdaq100_is_a_quality_gated_screener_source():
    assert SCREENER_INDEXES["NASDAQ100"] == "NDX.INDX"
    assert SCREENER_UNIVERSE == "RUSSELL3000_NASDAQ100"
    with pytest.raises(ValueError, match="Nasdaq-100 component universe is too small"):
        _validate_index_components("Nasdaq-100", ["MELI.US"], 2)


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
            "PEGRatio": 1.25,
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
                report_date: {"epsActual": 2 if report_date.startswith("2025") else 1}
                for report_date in (
                    "2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31",
                    "2024-12-31", "2024-09-30", "2024-06-30", "2024-03-31",
                )
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
                    report_date: {
                        "totalRevenue": 125,
                        "grossProfit": 50,
                        "operatingIncome": 30,
                        "netIncome": 25,
                        "ebit": 30,
                        "incomeTaxExpense": 6,
                        "incomeBeforeTax": 30,
                    }
                    for report_date in (
                        "2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31",
                        "2024-12-31", "2024-09-30", "2024-06-30", "2024-03-31",
                    )
                },
                "yearly": {
                    f"{year}-12-31": {"totalRevenue": 100 * (year - 2018)}
                    for year in range(2019, 2026)
                },
            },
            "Cash_Flow": {
                "quarterly": {
                    report_date: {"freeCashFlow": 10}
                    for report_date in (
                        "2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31",
                    )
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
    assert metrics["peg_ratio_raw"] == pytest.approx(1.25)
    assert metrics["peg_ratio"] == pytest.approx(1.25)
    assert metrics["insider_ownership"] == pytest.approx(0.10)
    assert metrics["institutional_ownership"] == pytest.approx(0.70)
    assert metrics["eps_growth_ttm"] == pytest.approx(1)
    assert metrics["roic"] == pytest.approx(0.192)


def test_fundamental_extractor_preserves_meaningful_zeroes_and_rejects_zero_pe():
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
    assert metrics["pe_ratio"] is None
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


def test_fundamental_extractor_normalizes_invalid_ratios_without_clipping_valid_outliers():
    metrics = extract_fundamental_metrics({
        "Highlights": {
            "MarketCapitalization": 1_000,
            "PERatio": -4,
            "ReturnOnEquityTTM": 12,
            "RevenueTTM": 0,
            "OperatingMarginTTM": -500,
            "ProfitMargin": -600,
            "EarningsShare": -1,
            "QuarterlyRevenueGrowthYOY": 99,
        },
        "Valuation": {
            "ForwardPE": 0,
            "PriceSalesTTM": 999_999.9999,
            "PriceBookMRQ": 3,
            "EnterpriseValueEbitda": -2,
            "EnterpriseValueRevenue": 999_999.9999,
        },
        "SharesStats": {
            "ShortPercentFloat": -0.1,
            "PercentInstitutions": 146.875,
        },
        "SplitsDividends": {"PayoutRatio": 0.5},
        "AnalystRatings": {"Rating": 6, "TargetPrice": 0},
        "Financials": {
            "Balance_Sheet": {
                "quarterly": {
                    "2025-12-31": {
                        "totalCurrentAssets": 50,
                        "totalCurrentLiabilities": 25,
                        "inventory": 75,
                        "totalStockholderEquity": -10,
                    },
                },
            },
        },
    })

    for field_name in (
        "pe_ratio",
        "forward_pe",
        "ps_ratio",
        "pb_ratio",
        "ev_ebitda",
        "ev_sales",
        "quick_ratio",
        "roe",
        "payout_ratio",
        "short_float",
        "analyst_recommendation",
        "target_price",
        "operating_margin",
        "net_profit_margin",
    ):
        assert metrics[field_name] is None
    assert metrics["current_ratio"] == pytest.approx(2)
    assert metrics["institutional_ownership"] == pytest.approx(1.46875)


def test_fundamental_extractor_preserves_negative_margins_and_zero_payout_with_valid_bases():
    metrics = extract_fundamental_metrics({
        "Highlights": {
            "RevenueTTM": 100,
            "GrossProfitTTM": -50,
            "OperatingMarginTTM": -2,
            "ProfitMargin": -3,
        },
        "SplitsDividends": {"PayoutRatio": 0},
        "Financials": {
            "Income_Statement": {
                "yearly": {
                    "2025-12-31": {
                        "totalRevenue": 100,
                        "netIncome": 10,
                    },
                },
            },
        },
    })

    assert metrics["gross_margin"] == pytest.approx(-0.5)
    assert metrics["operating_margin"] == pytest.approx(-2)
    assert metrics["net_profit_margin"] == pytest.approx(-3)
    assert metrics["payout_ratio"] == 0


def test_sales_growth_requires_a_positive_comparison_base():
    current_dates = ("2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31")
    prior_dates = ("2024-12-31", "2024-09-30", "2024-06-30", "2024-03-31")
    metrics = extract_fundamental_metrics({
        "Highlights": {"RevenueTTM": 400, "QuarterlyRevenueGrowthYOY": 99},
        "Financials": {
            "Income_Statement": {
                "quarterly": {
                    **{report_date: {"totalRevenue": 100} for report_date in current_dates},
                    **{report_date: {"totalRevenue": -1} for report_date in prior_dates},
                },
            },
        },
    })

    assert metrics["sales_growth_qoq"] is None
    assert metrics["sales_growth_ttm"] is None


@pytest.mark.parametrize(
    ("provider_value", "expected_canonical"),
    [(-1.5, None), (0, None), (1.5, 1.5), (None, None)],
)
def test_fundamental_extractor_keeps_raw_peg_but_only_exposes_positive_values(
    provider_value,
    expected_canonical,
):
    metrics = extract_fundamental_metrics({"Highlights": {"PEGRatio": provider_value}})

    assert metrics["peg_ratio_raw"] == provider_value
    assert metrics["peg_ratio"] == expected_canonical


def test_quarterly_growth_does_not_compare_different_fiscal_quarters():
    metrics = extract_fundamental_metrics({
        "Financials": {
            "Income_Statement": {
                "quarterly": {
                    "2025-12-31": {"totalRevenue": 120},
                    "2025-09-30": {"totalRevenue": 110},
                    "2025-06-30": {"totalRevenue": 100},
                    "2025-03-31": {"totalRevenue": 90},
                    "2024-09-30": {"totalRevenue": 80},
                },
            },
        },
        "Earnings": {
            "History": {
                "2025-12-31": {"epsActual": 1.20},
                "2025-09-30": {"epsActual": 1.10},
                "2025-06-30": {"epsActual": 1.00},
                "2025-03-31": {"epsActual": 0.90},
                "2024-09-30": {"epsActual": 0.80},
            },
        },
    })
    assert metrics["sales_growth_qoq"] is None
    assert metrics["eps_growth_qoq"] is None


def test_ttm_windows_require_consecutive_fiscal_quarters():
    quarterly_dates = (
        "2025-12-31",
        "2025-09-30",
        "2025-03-31",
        "2024-12-31",
        "2024-09-30",
        "2024-06-30",
        "2024-03-31",
        "2023-12-31",
    )
    metrics = extract_fundamental_metrics({
        "Financials": {
            "Income_Statement": {
                "quarterly": {
                    report_date: {
                        "totalRevenue": 100,
                        "grossProfit": 40,
                    }
                    for report_date in quarterly_dates
                },
            },
            "Cash_Flow": {
                "quarterly": {
                    report_date: {"freeCashFlow": 10}
                    for report_date in quarterly_dates[:4]
                },
            },
        },
        "Earnings": {
            "History": {
                report_date: {"epsActual": 1}
                for report_date in quarterly_dates
            },
        },
    })

    assert metrics["fcf"] is None
    assert metrics["gross_margin"] is None
    assert metrics["sales_growth_ttm"] is None
    assert metrics["eps_growth_ttm"] is None


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

    mixed_tax_windows = extract_fundamental_metrics({
        "Financials": {
            "Balance_Sheet": {
                "quarterly": {
                    "2025-12-31": {"netInvestedCapital": 200},
                },
            },
            "Income_Statement": {
                "quarterly": {
                    "2025-12-31": {"ebit": 10, "incomeTaxExpense": 2},
                    "2025-09-30": {"ebit": 10, "incomeTaxExpense": 2},
                    "2025-06-30": {"ebit": 10, "incomeTaxExpense": 2},
                    "2025-03-31": {"ebit": 10, "incomeTaxExpense": 2},
                },
                "yearly": {
                    "2025-12-31": {
                        "ebit": 40,
                        "incomeTaxExpense": 10,
                        "incomeBeforeTax": 50,
                    },
                },
            },
        },
    })
    assert mixed_tax_windows["roic"] == pytest.approx(0.16)

    mixed_revenue_windows = extract_fundamental_metrics({
        "Financials": {
            "Income_Statement": {
                "quarterly": {
                    "2025-12-31": {"totalRevenue": 100},
                    "2025-09-30": {"totalRevenue": 100},
                    "2025-06-30": {"totalRevenue": 100},
                    "2025-03-31": {},
                    "2024-12-31": {"totalRevenue": 50},
                    "2024-09-30": {"totalRevenue": 50},
                    "2024-06-30": {"totalRevenue": 50},
                    "2024-03-31": {"totalRevenue": 50},
                },
                "yearly": {
                    "2025-12-31": {"totalRevenue": 600},
                    "2024-12-31": {"totalRevenue": 500},
                },
            },
        },
    })
    assert mixed_revenue_windows["sales_growth_ttm"] == pytest.approx(0.2)

    missing_fiscal_year = extract_fundamental_metrics({
        "Financials": {
            "Income_Statement": {
                "yearly": {
                    "2025-12-31": {"totalRevenue": 600},
                    "2024-12-31": {"totalRevenue": 500},
                    "2022-12-31": {"totalRevenue": 300},
                    "2021-12-31": {"totalRevenue": 200},
                    "2020-12-31": {"totalRevenue": 100},
                },
            },
        },
        "Earnings": {
            "Annual": {
                "2025-12-31": {"epsActual": 6},
                "2024-12-31": {"epsActual": 5},
                "2022-12-31": {"epsActual": 3},
                "2021-12-31": {"epsActual": 2},
                "2020-12-31": {"epsActual": 1},
            },
        },
    })
    assert missing_fiscal_year["sales_growth_3yr"] == pytest.approx(2 ** (1 / 3) - 1)
    assert missing_fiscal_year["sales_growth_5yr"] == pytest.approx(6 ** (1 / 5) - 1)
    assert missing_fiscal_year["eps_growth_3yr"] == pytest.approx(2 ** (1 / 3) - 1)
    assert missing_fiscal_year["eps_growth_5yr"] == pytest.approx(6 ** (1 / 5) - 1)

    annual_balance_metrics = extract_fundamental_metrics({
        "Highlights": {
            "MarketCapitalization": 1_000,
            "RevenueTTM": 1_000,
        },
        "Financials": {
            "Balance_Sheet": {
                "yearly": {
                    "2025-12-31": {
                        "cashAndShortTermInvestments": 100,
                        "totalCurrentAssets": 300,
                        "totalCurrentLiabilities": 150,
                        "inventory": 30,
                        "totalStockholderEquity": 400,
                        "shortLongTermDebtTotal": 200,
                    },
                },
            },
            "Income_Statement": {
                "yearly": {
                    "2025-12-31": {
                        "totalRevenue": 500,
                        "grossProfit": 200,
                        "operatingIncome": 100,
                        "netIncome": 50,
                    },
                },
            },
        },
    })
    assert annual_balance_metrics["price_cash"] == pytest.approx(10)
    assert annual_balance_metrics["current_ratio"] == pytest.approx(2)
    assert annual_balance_metrics["quick_ratio"] == pytest.approx(1.8)
    assert annual_balance_metrics["debt_to_equity"] == pytest.approx(0.5)
    assert annual_balance_metrics["gross_margin"] == pytest.approx(0.4)
    assert annual_balance_metrics["operating_margin"] == pytest.approx(0.2)
    assert annual_balance_metrics["net_profit_margin"] == pytest.approx(0.1)

    negative_eps_base = extract_fundamental_metrics({
        "Highlights": {
            "QuarterlyEarningsGrowthYOY": 2.0,
        },
        "Earnings": {
            "History": {
                "2025-12-31": {"epsActual": 1},
                "2025-09-30": {"epsActual": 1},
                "2025-06-30": {"epsActual": 1},
                "2025-03-31": {"epsActual": 1},
                "2024-12-31": {"epsActual": -1},
                "2024-09-30": {"epsActual": -1},
                "2024-06-30": {"epsActual": -1},
                "2024-03-31": {"epsActual": -1},
            },
        },
    })
    assert negative_eps_base["eps_growth_qoq"] is None
    assert negative_eps_base["eps_growth_ttm"] is None


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
        ([(12, 12.2, 9.8, 10), (9.5, 9.8, 9.3, 9.6), (11.7, 12, 11.4, 11.5)], None),
        ([(10, 12.2, 9.8, 12), (12.2, 12.4, 11.9, 12.1), (10.3, 10.6, 10.2, 10.5)], None),
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
    assert metrics["technical_quality"] == "ok"
    assert metrics["ma200"] is not None
    assert metrics["performance_1yr"] is not None
    assert metrics["volatility_1m"] is not None
    expected_weekly_range = sum(
        3 / (99 + index)
        for index in range(255, 260)
    ) / 5
    expected_monthly_range = sum(
        3 / (99 + index)
        for index in range(239, 260)
    ) / 21
    assert metrics["volatility_1w"] == pytest.approx(expected_weekly_range)
    assert metrics["volatility_1m"] == pytest.approx(expected_monthly_range)
    assert metrics["atr_14"] is not None
    assert metrics["high_52w_rel"] <= 0
    assert metrics["low_52w_rel"] >= 0

    short_history = calculate_price_metrics(rows.tail(10))
    assert short_history["average_volume_3m"] is None
    assert short_history["relative_volume"] is None


def test_price_metrics_quarantine_impossible_adjusted_returns():
    rows = pd.DataFrame({
        "date": [value.date() for value in pd.bdate_range("2025-01-01", periods=30)],
        "open": [100] * 29 + [1_200],
        "high": [101] * 29 + [1_210],
        "low": [99] * 29 + [1_190],
        "close": [100] * 29 + [1_200],
        "adjusted_close": [100] * 29 + [1_200],
        "volume": [1_000] * 30,
    })

    metrics = calculate_price_metrics(rows)

    assert metrics["technical_quality"] == "extreme_adjusted_return"
    assert all(
        value is None
        for field_name, value in metrics.items()
        if field_name != "technical_quality"
    )


def test_price_metrics_quarantine_invalid_ohlc_instead_of_emitting_outliers():
    rows = pd.DataFrame({
        "date": [date(2025, 1, 1), date(2025, 1, 2)],
        "open": [100, 100],
        "high": [101, 90],
        "low": [99, 80],
        "close": [100, 100],
        "adjusted_close": [100, 100],
        "volume": [1_000, 1_000],
    })

    metrics = calculate_price_metrics(rows)

    assert metrics["technical_quality"] == "invalid_ohlc"
    assert metrics["performance_1d"] is None


def test_beta_does_not_forward_fill_missing_closes():
    dates = pd.bdate_range("2025-01-01", periods=130)
    benchmark_prices = pd.Series(
        [100 + index + index ** 2 / 100 for index in range(130)],
        index=dates,
    )
    asset_prices = benchmark_prices.copy()
    asset_prices.iloc[50] = None
    rows = pd.DataFrame({
        "date": [value.date() for value in dates],
        "open": asset_prices.values,
        "high": asset_prices.values + 1,
        "low": asset_prices.values - 1,
        "close": asset_prices.values,
        "adjusted_close": asset_prices.values,
        "volume": [1_000] * 130,
    })
    benchmark_returns = benchmark_prices.pct_change(fill_method=None)
    asset_returns = asset_prices.pct_change(fill_method=None)
    aligned = pd.concat(
        [asset_returns.rename("asset"), benchmark_returns.rename("benchmark")],
        axis=1,
    ).dropna()
    expected = aligned.cov().loc["asset", "benchmark"] / aligned["benchmark"].var()

    assert calculate_price_metrics(rows, benchmark_returns)["beta_1yr"] == pytest.approx(expected)


def test_price_metrics_do_not_mix_raw_and_adjusted_prices():
    rows = pd.DataFrame({
        "date": [value.date() for value in pd.bdate_range("2025-01-01", periods=20)],
        "open": [100] * 20,
        "high": [101] * 20,
        "low": [99] * 20,
        "close": [100] * 20,
        "adjusted_close": [50] * 19 + [None],
        "volume": [1_000] * 20,
    })

    metrics = calculate_price_metrics(rows)

    assert metrics["ma20"] is None
    assert metrics["performance_1d"] is None
    assert metrics["atr_14"] is None


def test_atr_uses_wilder_initial_average():
    ranges = list(range(1, 16))
    rows = pd.DataFrame({
        "date": [value.date() for value in pd.bdate_range("2025-01-01", periods=15)],
        "open": [100] * 15,
        "high": [100 + value / 2 for value in ranges],
        "low": [100 - value / 2 for value in ranges],
        "close": [100] * 15,
        "adjusted_close": [100] * 15,
        "volume": [1_000] * 15,
    })
    initial_atr = sum(ranges[:14]) / 14
    expected = (initial_atr * 13 + ranges[14]) / 14
    assert calculate_price_metrics(rows)["atr_14"] == pytest.approx(expected)


def test_atr_requires_a_contiguous_complete_price_window():
    rows = pd.DataFrame({
        "date": [value.date() for value in pd.bdate_range("2025-01-01", periods=15)],
        "open": [100] * 15,
        "high": [101] * 15,
        "low": [99] * 15,
        "close": [100] * 15,
        "adjusted_close": [100] * 15,
        "volume": [1_000] * 15,
    })
    rows.loc[1, "high"] = None

    assert calculate_price_metrics(rows)["atr_14"] is None


def test_price_range_distances_require_complete_ohlc_windows():
    rows = pd.DataFrame({
        "date": [value.date() for value in pd.bdate_range("2024-01-01", periods=252)],
        "open": [100] * 252,
        "high": [101] * 252,
        "low": [99] * 252,
        "close": [100] * 252,
        "adjusted_close": [100] * 252,
        "volume": [1_000] * 252,
    })
    rows.loc[240, "high"] = None

    metrics = calculate_price_metrics(rows)

    assert metrics["high_20d_rel"] is None
    assert metrics["low_20d_rel"] is None
    assert metrics["high_50d_rel"] is None
    assert metrics["low_50d_rel"] is None
    assert metrics["high_52w_rel"] is None
    assert metrics["low_52w_rel"] is None


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


def test_dividend_growth_uses_the_years_final_market_session():
    actions = [
        (date(2021, 3, 1), 1.0),
        (date(2022, 3, 1), 1.2),
    ]
    metrics = calculate_dividend_growth(actions, date(2022, 12, 30))
    assert metrics["dividend_growth_1yr"] == pytest.approx(0.2)


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
            exchange="NASDAQ",
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
            exchange="NYSE",
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
            source=LIVE_UNIVERSE_SOURCE,
            source_run_id=run.id,
        ),
        UniverseMembership(
            universe="RUSSELL2000",
            ticker="NOT-IN-SNAPSHOT.US",
            effective_from=as_of,
            source=LIVE_UNIVERSE_SOURCE,
            source_run_id=run.id,
        ),
    ])
    await db_session.commit()

    metadata_statements = []

    def track_metadata_queries(*args):
        metadata_statements.append(args[2])

    event.listen(db_session.bind.sync_engine, "before_cursor_execute", track_metadata_queries)
    try:
        metadata = await get_screener_metadata(db_session)
    finally:
        event.remove(db_session.bind.sync_engine, "before_cursor_execute", track_metadata_queries)

    assert len(metadata_statements) <= 10
    assert metadata["supported_finviz_fields"] == 66
    fcf_metadata = next(field for field in metadata["fields"] if field["id"] == "fcf")
    assert fcf_metadata["finviz_field"] is None
    assert metadata["record_count"] == 2
    assert any(field["id"] == "pe_ratio" and field["available"] for field in metadata["fields"])
    peg_metadata = next(field for field in metadata["fields"] if field["id"] == "peg_ratio")
    assert peg_metadata["label"] == "PEG (5Y Expected)"
    assert peg_metadata["presets"] == [
        {"label": "Below 1", "operator": "lt", "value": 1},
        {"label": "Below 2", "operator": "lt", "value": 2},
        {"label": "3 or more", "operator": "gte", "value": 3},
    ]
    assert "at or below 0" in peg_metadata["description"]
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

    with pytest.raises(ValueError, match="at most 30 optional"):
        await query_screener({
            "columns": [f"field_{index}" for index in range(31)],
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

        default_direction_response = await client.post("/api/stocks/screener/query", json={
            "sort": {"field": "pe_ratio"},
            "columns": ["pe_ratio"],
        })
        assert default_direction_response.status_code == 200
        assert [
            item["ticker"]
            for item in default_direction_response.json()["items"]
        ] == ["BBB.US", "AAA.US"]

        invalid_response = await client.post("/api/stocks/screener/query", json={
            "filters": [{"field": "drop_table", "operator": "eq", "value": 1}],
        })
        assert invalid_response.status_code == 422

        invalid_operand_response = await client.post("/api/stocks/screener/query", json={
            "filters": [{"field": "pe_ratio", "operator": "gte", "value": None}],
        })
        assert invalid_operand_response.status_code == 422

        oversized_in_response = await client.post("/api/stocks/screener/query", json={
            "filters": [{
                "field": "sector",
                "operator": "in",
                "value": [f"sector-{index}" for index in range(101)],
            }],
        })
        assert oversized_in_response.status_code == 422

        oversized_offset_response = await client.post("/api/stocks/screener/query", json={
            "offset": 1_000_001,
        })
        assert oversized_offset_response.status_code == 422

        unavailable_date_response = await client.post("/api/stocks/screener/query", json={
            "as_of_date": "0001-01-01",
        })
        assert unavailable_date_response.status_code == 200
        assert unavailable_date_response.json()["freshness"] is None

        invalid_date_operand_response = await client.post("/api/stocks/screener/query", json={
            "filters": [{"field": "ipo_date", "operator": "eq", "value": ["2020-01-15"]}],
        })
        assert invalid_date_operand_response.status_code == 422

        too_many_columns_response = await client.post("/api/stocks/screener/query", json={
            "columns": [f"field_{index}" for index in range(31)],
        })
        assert too_many_columns_response.status_code == 422

        empty_columns_response = await client.post("/api/stocks/screener/query", json={
            "columns": [],
        })
        assert empty_columns_response.status_code == 200
        assert set(empty_columns_response.json()["items"][0]) == {"ticker", "name"}


@pytest.mark.asyncio
async def test_query_defensively_normalizes_legacy_values_and_hides_otc_rows(db_session):
    as_of = date.today() - timedelta(days=1)
    db_session.add_all([
        StockScreenerSnapshot(
            ticker="ZERO.US",
            name="Zero Sentinel",
            date=as_of,
            exchange="NASDAQ",
            market_cap=100,
            close=10,
            pe_ratio=0,
            target_price=0,
            quick_ratio=-1,
            ps_ratio=999_999.9999,
            ev_ebitda=-2,
        ),
        StockScreenerSnapshot(
            ticker="VALID.US",
            name="Valid",
            date=as_of,
            exchange="NYSE",
            market_cap=200,
            close=20,
            pe_ratio=10,
            target_price=25,
            quick_ratio=1.5,
            ps_ratio=2,
            ev_ebitda=8,
        ),
        StockScreenerSnapshot(
            ticker="OTC.US",
            name="OTC",
            date=as_of,
            exchange="PINK",
            market_cap=300,
            close=1,
            pe_ratio=5,
        ),
        StockScreenerSnapshot(
            ticker="UNKNOWN.US",
            name="Unknown venue",
            date=as_of,
            exchange=None,
            market_cap=400,
            close=2,
            pe_ratio=4,
        ),
    ])
    await db_session.commit()

    metadata = await get_screener_metadata(db_session)
    pe_metadata = next(field for field in metadata["fields"] if field["id"] == "pe_ratio")
    assert metadata["record_count"] == 2
    assert pe_metadata["coverage"] == pytest.approx(0.5)

    result = await query_screener({
        "columns": [
            "pe_ratio",
            "target_price",
            "quick_ratio",
            "ps_ratio",
            "ev_ebitda",
        ],
        "sort": {"field": "market_cap", "direction": "asc"},
    }, db_session)

    assert result["total"] == 2
    assert [item["ticker"] for item in result["items"]] == ["ZERO.US", "VALID.US"]
    assert all(
        result["items"][0][field_name] is None
        for field_name in (
            "pe_ratio",
            "target_price",
            "quick_ratio",
            "ps_ratio",
            "ev_ebitda",
        )
    )

    cheap = await query_screener({
        "filters": [{"field": "pe_ratio", "operator": "lte", "value": 1}],
        "columns": ["pe_ratio"],
    }, db_session)
    assert cheap["total"] == 0


@pytest.mark.asyncio
async def test_price_vs_sma_filters_and_coverage_use_canonical_operands(db_session):
    as_of = date.today() - timedelta(days=1)
    db_session.add_all([
        StockScreenerSnapshot(
            ticker="VALID.US",
            name="Valid",
            date=as_of,
            exchange="NASDAQ",
            market_cap=300,
            close=20,
            ma50=10,
        ),
        StockScreenerSnapshot(
            ticker="BAD-CLOSE.US",
            name="Bad close",
            date=as_of,
            exchange="NYSE",
            market_cap=200,
            close=-1,
            ma50=-2,
        ),
        StockScreenerSnapshot(
            ticker="BAD-MA.US",
            name="Bad moving average",
            date=as_of,
            exchange="NYSE",
            market_cap=100,
            close=10,
            ma50=0,
        ),
    ])
    await db_session.commit()

    metadata = await get_screener_metadata(db_session)
    comparison = next(
        field for field in metadata["fields"] if field["id"] == "price_vs_ma50"
    )
    assert comparison["coverage"] == pytest.approx(1 / 3)

    result = await query_screener({
        "filters": [{"field": "price_vs_ma50", "operator": "eq", "value": "above"}],
        "columns": ["close", "ma50"],
    }, db_session)
    assert result["total"] == 1
    assert result["items"][0]["ticker"] == "VALID.US"


@pytest.mark.asyncio
async def test_technicals_skip_price_history_that_does_not_reach_snapshot(db_session):
    snapshot_date = date(2025, 1, 10)
    db_session.add(Ticker(ticker="AAA.US"))
    db_session.add(DailyPrice(
        ticker="AAA.US",
        date=snapshot_date - timedelta(days=1),
        open=99,
        high=101,
        low=98,
        close=100,
        adjusted_close=100,
        volume=1_000,
    ))
    await db_session.commit()

    technicals = await calculate_technicals_locally(
        db_session,
        ["AAA.US"],
        as_of_date=snapshot_date,
    )
    assert technicals.empty


@pytest.mark.asyncio
async def test_technicals_do_not_replace_missing_adjusted_closes(db_session):
    dates = [value.date() for value in pd.bdate_range("2025-01-02", periods=20)]
    db_session.add(Ticker(ticker="AAA.US"))
    db_session.add_all([
        DailyPrice(
            ticker="AAA.US",
            date=price_date,
            open=100,
            high=101,
            low=99,
            close=100,
            adjusted_close=100 if index < 19 else None,
            volume=1_000,
        )
        for index, price_date in enumerate(dates)
    ])
    await db_session.commit()

    technicals = await calculate_technicals_locally(
        db_session,
        ["AAA.US"],
        as_of_date=dates[-1],
    )

    assert len(technicals) == 1
    assert pd.isna(technicals.iloc[0]["ma20"])
    assert pd.isna(technicals.iloc[0]["performance_1d"])


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_adjusted_close", [None, 0, 1_200])
async def test_technicals_reject_contaminated_benchmark_returns_for_beta(
    db_session,
    bad_adjusted_close,
):
    dates = [value.date() for value in pd.bdate_range("2025-01-02", periods=130)]
    db_session.add_all([Ticker(ticker="AAA.US"), Ticker(ticker="SPY.US")])
    db_session.add_all([
        DailyPrice(
            ticker="AAA.US",
            date=price_date,
            open=100 + index / 10,
            high=101 + index / 10,
            low=99 + index / 10,
            close=100 + index / 10,
            adjusted_close=100 + index / 10,
            volume=1_000,
        )
        for index, price_date in enumerate(dates)
    ])
    db_session.add_all([
        DailyPrice(
            ticker="SPY.US",
            date=price_date,
            close=100,
            adjusted_close=bad_adjusted_close if index == len(dates) - 1 else 100,
        )
        for index, price_date in enumerate(dates)
    ])
    await db_session.commit()

    technicals = await calculate_technicals_locally(
        db_session,
        ["AAA.US"],
        as_of_date=dates[-1],
    )

    assert len(technicals) == 1
    assert technicals.iloc[0]["technical_quality"] == "ok"
    assert pd.isna(technicals.iloc[0]["beta_1yr"])
    assert technicals.iloc[0]["ma50"] > 0


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
            exchange="NASDAQ",
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
async def test_index_metadata_accepts_live_memberships_without_pit_history(db_session):
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
            exchange="NASDAQ",
            close=100,
            volume=1_000,
        ),
        StockScreenerSnapshot(
            ticker="STALE.US",
            name="Stale historical member",
            date=as_of,
            exchange="NYSE",
            close=50,
            volume=1_000,
        ),
        StockScreenerSnapshot(
            ticker="MELI.US",
            name="MercadoLibre",
            date=as_of,
            exchange="NASDAQ",
            close=2_000,
            volume=1_000,
        ),
        UniverseMembership(
            universe="SP500",
            ticker="AAA.US",
            effective_from=as_of,
            source=LIVE_UNIVERSE_SOURCE,
            source_run_id=run.id,
        ),
        UniverseMembership(
            universe="NASDAQ100",
            ticker="MELI.US",
            effective_from=as_of,
            source=LIVE_UNIVERSE_SOURCE,
            source_run_id=run.id,
        ),
        UniverseMembership(
            universe="RUSSELL1000",
            ticker="AAA.US",
            effective_from=as_of,
            source=LIVE_UNIVERSE_SOURCE,
            source_run_id=run.id,
        ),
        UniverseMembership(
            universe="RUSSELL3000",
            ticker="AAA.US",
            effective_from=as_of,
            source=LIVE_UNIVERSE_SOURCE,
            source_run_id=run.id,
        ),
        UniverseMembership(
            universe="SP500",
            ticker="STALE.US",
            effective_from=as_of,
            source=HISTORICAL_UNIVERSE_SOURCE,
            source_run_id=run.id,
        ),
    ])
    await db_session.commit()

    metadata = await get_screener_metadata(db_session)
    assert metadata["universe"] == "RUSSELL3000_NASDAQ100"
    index_field = next(field for field in metadata["fields"] if field["id"] == "index")
    assert index_field["available"] is True
    assert index_field["coverage"] == pytest.approx(2 / 3)
    assert index_field["options"] == [
        {"value": "SP500", "label": "S&P 500"},
        {"value": "RUSSELL1000", "label": "Russell 1000"},
        {"value": "RUSSELL3000", "label": "Russell 3000"},
        {"value": "NASDAQ100", "label": "Nasdaq-100"},
    ]

    result = await query_screener({
        "filters": [{"field": "index", "operator": "eq", "value": "SP500"}],
        "columns": [],
    }, db_session)
    assert result["total"] == 1
    assert result["items"][0]["ticker"] == "AAA.US"

    for universe in ("RUSSELL1000", "RUSSELL3000"):
        result = await query_screener({
            "filters": [{"field": "index", "operator": "eq", "value": universe}],
            "columns": [],
        }, db_session)
        assert result["total"] == 1
        assert result["items"][0]["ticker"] == "AAA.US"

    nasdaq_result = await query_screener({
        "filters": [{"field": "index", "operator": "eq", "value": "NASDAQ100"}],
        "columns": [],
    }, db_session)
    assert nasdaq_result["items"] == [{"ticker": "MELI.US", "name": "MercadoLibre"}]

    russell_result = await query_screener({
        "filters": [{"field": "index", "operator": "eq", "value": "RUSSELL3000"}],
        "columns": [],
    }, db_session)
    assert all(item["ticker"] != "MELI.US" for item in russell_result["items"])


@pytest.mark.asyncio
async def test_pinned_latest_legacy_snapshot_remains_queryable(db_session):
    snapshot_date = date(2025, 1, 10)
    db_session.add(StockScreenerSnapshot(
        ticker="AAA.US",
        name="Alpha",
        date=snapshot_date,
        exchange="NASDAQ",
        close=100,
        volume=1_000,
    ))
    await db_session.commit()

    metadata = await get_screener_metadata(db_session)
    assert metadata["as_of_date"] == snapshot_date.isoformat()
    assert metadata["universe"] == "RUSSELL3000"
    result = await query_screener({
        "as_of_date": snapshot_date.isoformat(),
        "columns": [],
    }, db_session)
    assert result["total"] == 1
    assert result["items"] == [{"ticker": "AAA.US", "name": "Alpha"}]

    unavailable_date = await query_screener({
        "as_of_date": (snapshot_date - timedelta(days=1)).isoformat(),
        "columns": [],
    }, db_session)
    assert unavailable_date["total"] == 0


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
