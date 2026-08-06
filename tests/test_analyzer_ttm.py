from datetime import date

import pytest

from models import DailyPrice, FinancialStatement, Ticker
from services.analyzer import calculate_ttm, get_fundamental_valuation


def _quarter(
    fiscal_date: date,
    *,
    revenue: float,
    gross_profit: float,
    net_income: float,
    free_cash_flow: float,
) -> FinancialStatement:
    return FinancialStatement(
        ticker="TEST.US",
        fiscal_date=fiscal_date,
        period="Quarterly",
        revenue=revenue,
        net_income=net_income,
        income_statement={
            "totalRevenue": revenue,
            "grossProfit": gross_profit,
            "netIncome": net_income,
        },
        balance_sheet={},
        cash_flow={"freeCashFlow": free_cash_flow},
    )


def _latest_balance_sheet() -> FinancialStatement:
    return FinancialStatement(
        ticker="TEST.US",
        fiscal_date=date(2025, 9, 30),
        period="Quarterly",
        balance_sheet={
            "totalAssets": 1_000,
            "totalLiabilities": 400,
            "totalStockholderEquity": 600,
            "commonStockSharesOutstanding": 100,
            "cashAndCashEquivalents": 80,
            "totalDebt": 120,
        },
    )


def test_calculate_ttm_sums_unsorted_contiguous_quarters_across_year_boundary():
    records = [
        _quarter(date(2024, 9, 30), revenue=20, gross_profit=8, net_income=2, free_cash_flow=1),
        _quarter(date(2025, 3, 31), revenue=40, gross_profit=16, net_income=4, free_cash_flow=3),
        _quarter(date(2024, 6, 30), revenue=10, gross_profit=4, net_income=1, free_cash_flow=0.5),
        _quarter(date(2024, 12, 31), revenue=30, gross_profit=12, net_income=3, free_cash_flow=2),
    ]

    result = calculate_ttm(records, _latest_balance_sheet())

    assert result["ttm_revenue"] == pytest.approx(100)
    assert result["ttm_gross_profit"] == pytest.approx(40)
    assert result["ttm_net_income"] == pytest.approx(10)
    assert result["ttm_fcf"] == pytest.approx(6.5)
    assert result["roe"] == pytest.approx(10 / 600)


def test_calculate_ttm_rejects_four_quarters_with_a_gap(caplog):
    records = [
        _quarter(date(2025, 9, 30), revenue=40, gross_profit=16, net_income=4, free_cash_flow=3),
        _quarter(date(2023, 9, 30), revenue=30, gross_profit=12, net_income=3, free_cash_flow=2),
        _quarter(date(2023, 6, 30), revenue=20, gross_profit=8, net_income=2, free_cash_flow=1),
        _quarter(date(2023, 3, 31), revenue=10, gross_profit=4, net_income=1, free_cash_flow=0.5),
    ]

    result = calculate_ttm(records, _latest_balance_sheet())

    assert result["ttm_revenue"] == 0
    assert result["ttm_gross_profit"] == 0
    assert result["ttm_net_income"] == 0
    assert result["ttm_fcf"] == 0
    assert result["roe"] == 0
    assert result["total_assets"] == 1_000
    assert result["total_equity"] == 600
    assert "quarterly statements are not contiguous" in caplog.text


@pytest.mark.asyncio
async def test_ttm_gross_margin_anomaly_preserves_reported_value_and_warns(db_session):
    ticker = "MARGIN.US"
    db_session.add(Ticker(ticker=ticker, name="Margin Test", currency="USD"))
    quarter_dates = [
        date(2025, 12, 31),
        date(2025, 9, 30),
        date(2025, 6, 30),
        date(2025, 3, 31),
    ]
    quarters = [
        _quarter(
            period_end,
            revenue=100,
            gross_profit=10,
            net_income=5,
            free_cash_flow=4,
        )
        for period_end in quarter_dates
    ]
    for quarter in quarters:
        quarter.ticker = ticker
    balance = _latest_balance_sheet().balance_sheet
    quarters[0].balance_sheet = balance
    yearly = FinancialStatement(
        ticker=ticker,
        fiscal_date=date(2024, 12, 31),
        period="Yearly",
        revenue=400,
        net_income=40,
        income_statement={
            "totalRevenue": 400,
            "grossProfit": 200,
            "netIncome": 40,
        },
        balance_sheet=balance,
        cash_flow={"freeCashFlow": 30},
    )
    db_session.add_all([
        *quarters,
        yearly,
        DailyPrice(
            ticker=ticker,
            date=date(2026, 1, 2),
            close=10,
            adjusted_close=10,
        ),
    ])
    await db_session.commit()

    result = await get_fundamental_valuation(ticker, db_session)

    assert result["ttm"]["gross_profit"] == 40
    assert result["ttm"]["gross_profit"] != 200
    assert result["data_quality_warnings"][0]["code"] == "ttm_gross_margin_deviation"
