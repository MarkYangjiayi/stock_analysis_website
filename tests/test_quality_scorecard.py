import pytest
from fastapi.testclient import TestClient

from services.quality_scorecard import (
    SCREENER_FIELDS,
    annual_rows,
    build_quality_scorecard,
    piotroski,
    screener_values,
    template_for,
)
from services.raw_store import persist_snapshot
from services.screener_metrics import extract_fundamental_metrics

YEARS = ["2020-12-31", "2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"]


def _payload(rows, *, industry="Software - Application", market_cap=500e9):
    """rows: one dict per fiscal year (oldest first) with income, balance and cash-flow fields."""
    income, balance, cash = {}, {}, {}
    for day, row in zip(YEARS[-len(rows):], rows):
        income[day] = {"date": day, **{key: row.get(key) for key in (
            "totalRevenue", "grossProfit", "operatingIncome", "ebit", "ebitda", "netIncome",
            "interestExpense", "incomeTaxExpense", "incomeBeforeTax")}}
        balance[day] = {"date": day, **{key: row.get(key) for key in (
            "totalAssets", "totalLiab", "totalCurrentAssets", "totalCurrentLiabilities", "retainedEarnings",
            "totalStockholderEquity", "longTermDebtTotal", "shortLongTermDebtTotal", "cashAndShortTermInvestments",
            "netInvestedCapital", "commonStockSharesOutstanding")}}
        cash[day] = {"date": day, **{key: row.get(key) for key in (
            "totalCashFromOperatingActivities", "capitalExpenditures", "freeCashFlow", "stockBasedCompensation",
            "depreciation")}}
    return {
        "General": {"Code": "TEST", "Industry": industry},
        "Highlights": {"MarketCapitalization": market_cap},
        "Financials": {"Income_Statement": {"yearly": income}, "Balance_Sheet": {"yearly": balance},
                       "Cash_Flow": {"yearly": cash}},
    }


def _compounder(year):
    """Growing 10% a year, 30% operating margin, net cash, steady buybacks."""
    revenue = 100.0 * 1.10 ** year
    ebit = 0.30 * revenue
    return {
        "totalRevenue": revenue, "grossProfit": 0.60 * revenue + year, "operatingIncome": ebit, "ebit": ebit,
        "ebitda": ebit + 5, "netIncome": 0.75 * ebit, "interestExpense": 1.0, "incomeTaxExpense": 0.2 * ebit,
        "incomeBeforeTax": ebit, "totalAssets": 400.0, "totalLiab": 150.0 - year,
        "totalCurrentAssets": 200.0 + 5 * year, "totalCurrentLiabilities": 100.0, "retainedEarnings": 150.0 + 20 * year,
        "totalStockholderEquity": 250.0, "longTermDebtTotal": 30.0 - year, "shortLongTermDebtTotal": 40.0 - year,
        "cashAndShortTermInvestments": 120.0, "netInvestedCapital": 150.0, "commonStockSharesOutstanding": 1000.0 - 15 * year,
        "totalCashFromOperatingActivities": 0.80 * ebit, "capitalExpenditures": -0.02 * revenue,
        "freeCashFlow": 0.80 * ebit - 0.02 * revenue, "stockBasedCompensation": 0.02 * revenue,
    }


def _struggler(year):
    """Shrinking revenue, operating losses, net debt and heavy dilution."""
    revenue = 100.0 * 0.95 ** year
    ebit = -10.0 - year
    return {
        "totalRevenue": revenue, "grossProfit": 0.30 * revenue, "operatingIncome": ebit, "ebit": ebit, "ebitda": ebit + 4,
        "netIncome": ebit - 5, "interestExpense": 6.0, "incomeTaxExpense": 0.0, "incomeBeforeTax": ebit - 5,
        "totalAssets": 300.0, "totalLiab": 260.0, "totalCurrentAssets": 60.0, "totalCurrentLiabilities": 90.0,
        "retainedEarnings": -200.0 - 15 * year, "totalStockholderEquity": 40.0, "longTermDebtTotal": 150.0 + 5 * year,
        "shortLongTermDebtTotal": 170.0 + 5 * year, "cashAndShortTermInvestments": 20.0, "netInvestedCapital": 210.0,
        "commonStockSharesOutstanding": 500.0 * 1.15 ** year, "totalCashFromOperatingActivities": -4.0,
        "capitalExpenditures": -6.0, "freeCashFlow": -10.0, "stockBasedCompensation": 0.12 * revenue,
    }


def test_a_steady_compounder_scores_high_with_every_pillar():
    card = build_quality_scorecard(_payload([_compounder(year) for year in range(6)]))

    assert card["template"] == "standard" and card["available"] is True
    assert card["score"] >= 75 and card["grade"] == "优"
    assert [pillar["score"] is not None for pillar in card["pillars"]] == [True] * 5
    metrics = card["metrics"]
    assert metrics["roic_avg"]["value"] > 0.15 and metrics["roic_avg"]["score"] == 100.0
    assert metrics["revenue_cagr"]["value"] == pytest.approx(0.10)
    assert metrics["net_debt_to_ebitda"]["score"] == 100.0  # Net cash.
    assert metrics["share_change"]["value"] == pytest.approx(925 / 1000 - 1)
    assert card["piotroski"]["score"] == 9 and card["piotroski"]["available"] == 9
    assert card["altman"]["zone"] == "safe"
    assert card["fiscal_years"] == YEARS


def test_a_loss_making_diluter_scores_low_and_lands_in_distress():
    card = build_quality_scorecard(_payload([_struggler(year) for year in range(6)], market_cap=20e9 / 1e9))

    assert card["score"] < 25 and card["grade"] == "弱"
    pillars = {pillar["key"]: pillar["score"] for pillar in card["pillars"]}
    assert pillars["profitability"] == 0.0 and pillars["capital_allocation"] == 0.0
    # Net debt with negative EBITDA scores zero rather than being skipped.
    assert card["metrics"]["net_debt_to_ebitda"]["score"] == 0.0
    # Losses make FCF conversion meaningless, so it is left out instead of rewarded.
    assert card["metrics"]["fcf_conversion"]["value"] is None
    assert card["altman"]["zone"] == "distress"
    assert card["piotroski"]["score"] <= 3


def test_banks_use_roe_and_skip_cash_conversion_leverage_and_distress_scores():
    rows = [_compounder(year) for year in range(6)]
    card = build_quality_scorecard(_payload(rows, industry="Banks - Regional"))

    assert template_for("Banks - Regional") == "financial" and card["template"] == "financial"
    applicable = {pillar["key"]: pillar["applicable"] for pillar in card["pillars"]}
    assert applicable == {"profitability": True, "growth": True, "cash_conversion": False,
                          "balance_sheet": False, "capital_allocation": True}
    assert card["pillars"][0]["metrics"] == ["roe_avg"]
    assert card["piotroski"] is None and card["altman"] is None
    assert "ROE" in card["notes"][0]


def test_reits_keep_the_f_score_but_not_altman_or_debt_to_ebitda():
    card = build_quality_scorecard(_payload([_compounder(year) for year in range(6)], industry="REIT - Retail"))

    assert card["template"] == "reit"
    balance = next(pillar for pillar in card["pillars"] if pillar["key"] == "balance_sheet")
    assert balance["metrics"] == ["interest_coverage"]
    assert card["piotroski"]["score"] is not None and card["altman"] is None


def test_missing_interest_with_debt_is_not_scored_and_no_debt_scores_full():
    rows = [_compounder(year) for year in range(6)]
    rows[-1] = {**rows[-1], "interestExpense": None}
    assert build_quality_scorecard(_payload(rows))["metrics"]["interest_coverage"]["score"] is None

    debt_free = [{**row, "shortLongTermDebtTotal": 0.0, "longTermDebtTotal": 0.0, "interestExpense": 0.0}
                 for row in (_compounder(year) for year in range(6))]
    assert build_quality_scorecard(_payload(debt_free))["metrics"]["interest_coverage"]["score"] == 100.0


def test_short_histories_and_missing_checks_are_reported_not_guessed():
    two_years = build_quality_scorecard(_payload([_compounder(0), _compounder(1)]))
    assert two_years["score"] is None and two_years["metrics"]["roic_avg"]["value"] is None
    assert any("多年指标和综合分需要至少 3 年" in note for note in two_years["notes"])

    rows = [_compounder(year) for year in range(6)]
    rows[-1] = {**rows[-1], "grossProfit": None}
    f_score = piotroski(annual_rows(_payload(rows)))
    assert f_score["score"] is None and f_score["available"] == 8

    assert build_quality_scorecard({"General": {}})["notes"] == ["年报少于两年，无法评分。"]


def test_screener_values_reach_the_screener_metrics():
    payload = _payload([_compounder(year) for year in range(6)])
    values = screener_values(payload)

    assert tuple(values) == SCREENER_FIELDS
    assert values["piotroski_f_score"] == 9 and values["quality_score"] >= 75
    metrics = extract_fundamental_metrics(payload)
    assert {key: metrics[key] for key in SCREENER_FIELDS} == values


@pytest.mark.asyncio
async def test_quality_endpoint_reads_the_latest_stored_payload(db_session):
    from main import app

    await persist_snapshot(db_session, "EODHD", "fundamentals", _payload([_compounder(year) for year in range(6)]),
                           details={"ticker": "QUAL.US"})
    await db_session.commit()
    with TestClient(app) as client:
        stored = client.get("/api/stocks/QUAL/quality").json()
        missing = client.get("/api/stocks/NONE/quality").json()

    assert stored["ticker"] == "QUAL.US" and stored["available"] is True and stored["grade"] == "优"
    assert stored["source"] == "EODHD fundamentals" and stored["fetched_at"]
    assert missing["available"] is False and missing["notes"] == ["No fundamentals snapshot is stored for this ticker."]


@pytest.mark.asyncio
async def test_rederive_script_fills_quality_fields_from_stored_payloads(db_session):
    import sys
    from datetime import date
    from pathlib import Path

    from sqlalchemy import select

    from models import ScreenerFundamentalCache, Ticker

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from rederive_expectation_signals import rederive

    snapshot = await persist_snapshot(db_session, "EODHD", "fundamentals", _payload([_compounder(year) for year in range(6)]),
                                      details={"ticker": "QUAL.US"})
    db_session.add(Ticker(ticker="QUAL.US"))
    await db_session.flush()
    db_session.add(ScreenerFundamentalCache(ticker="QUAL.US", as_of_date=date(2026, 10, 16), normalized_data={"pe_ratio": 20.0},
                                            raw_snapshot_id=snapshot.id, source="EODHD"))
    await db_session.commit()

    counts = await rederive(fields=SCREENER_FIELDS, derive=screener_values)
    assert counts["updated"] == 1

    db_session.expire_all()
    row = (await db_session.execute(select(ScreenerFundamentalCache).where(ScreenerFundamentalCache.ticker == "QUAL.US"))).scalar_one()
    assert row.normalized_data["pe_ratio"] == 20.0
    assert row.normalized_data["piotroski_f_score"] == 9 and row.normalized_data["quality_score"] >= 75
    assert (await rederive(fields=SCREENER_FIELDS, derive=screener_values))["already_present"] == 1
