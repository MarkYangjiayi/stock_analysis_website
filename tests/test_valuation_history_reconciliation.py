"""Historical quality failures must neither leak future facts nor hide good inputs."""
import copy
from datetime import date, datetime

import pytest

from tests.test_valuation_history import calculate, price, statements


def annual_statement():
    row = copy.deepcopy(statements()[-1])
    row.period_type = "Yearly"
    row.filing_at = row.available_at = datetime(2024, 11, 8)
    row.raw_snapshot_id = 2
    row.income_statement.update(netIncome=40, totalRevenue=400, ebitda=80)
    row.cash_flow.update(totalCashFromOperatingActivities=120, capitalExpenditures=40)
    return row


@pytest.mark.parametrize("revision", [False, True])
def test_fiscal_end_filing_is_not_verified_by_later_recorded_availability(revision):
    rows = statements()
    row = rows[-1]
    row.filing_at = datetime(2024, 9, 30)
    row.available_at = datetime(2024, 11, 1) if revision else row.filing_at
    row.revision = 2 if revision else 1
    for part in (row.income_statement, row.balance_sheet, row.cash_flow):
        part["filing_date"] = "2024-09-30"
    result = calculate(rows)
    assert all(v is None for key, v in result["points"][0]["values"].items() if key != "pb")
    assert result["points"][0]["values"]["pb"] == 2  # The disclosed June balance is still current.
    assert any("placeholder" in message for message in result["warnings"])
    assert result["metrics"][0]["median"] is None


def test_missing_quarter_is_not_skipped_when_older_quarters_exist():
    rows = statements()
    older = copy.deepcopy(rows[0])
    older.period_end = date(2023, 9, 30)
    older.filing_at = older.available_at = datetime(2023, 11, 1)
    rows[1].availability_estimated = True
    result = calculate([older, *rows])
    assert result["points"][0]["values"]["pe"] is None
    assert result["points"][0]["values"]["pb"] == 2


@pytest.mark.parametrize("part,field,total,affected", [
    ("income_statement", "totalRevenue", 300, {"ps", "ev_revenue"}),
    ("income_statement", "netIncome", 30, {"pe"}),
    ("income_statement", "ebitda", 60, {"ev_ebitda"}),
    ("cash_flow", "totalCashFromOperatingActivities", 100, {"pfcf"}),
    ("cash_flow", "capitalExpenditures", 30, {"pfcf"}),
])
def test_annual_conflict_only_gates_affected_metrics_after_disclosure(part, field, total, affected):
    annual = annual_statement()
    getattr(annual, part)[field] = total
    result = calculate(prices=[price("2024-11-07"), price("2024-11-08"), price("2024-11-11")], annual_statements=[annual])
    before, same_day, after = result["points"]
    assert all(v is not None for v in before["values"].values())
    assert same_day["values"] == before["values"]
    assert {key for key, v in after["values"].items() if v is None} == affected
    basis = result["bases"][after["basis_id"]]
    assert basis["raw_snapshot_ids"] == [1, 2]
    assert all("annual" in basis["reasons"][key] for key in affected)
    assert result["metrics"][0]["latest_value"] == after["values"]["pe"]


def test_annual_check_keeps_overlapping_ttm_gaps_and_applies_revisions_on_time():
    rows = statements()
    annual = annual_statement()
    annual.income_statement["totalRevenue"] = 300
    next_quarter = copy.deepcopy(rows[-1])
    next_quarter.period_end = date(2024, 12, 31)
    next_quarter.filing_at = next_quarter.available_at = datetime(2025, 2, 3)
    revised = copy.deepcopy(rows[1])
    revised.revision = 2
    revised.available_at = datetime(2025, 2, 5)
    revised.income_statement["totalRevenue"] = 0
    result = calculate([*rows, next_quarter, revised],
        prices=[price("2025-02-04"), price("2025-02-05"), price("2025-02-06")], annual_statements=[annual])
    assert [p["values"]["ps"] for p in result["points"]] == [None, None, pytest.approx(400 / 300)]
    assert result["metrics"][1]["median"] == pytest.approx(400 / 300)


def test_annual_revision_is_not_backdated_and_rounding_is_allowed():
    annual = annual_statement()
    annual.income_statement["totalRevenue"] = 399.5
    revised = copy.deepcopy(annual)
    revised.revision = 2
    revised.available_at = datetime(2024, 11, 12)
    revised.income_statement["totalRevenue"] = 300
    result = calculate(prices=[price("2024-11-11"), price("2024-11-12"), price("2024-11-13")], annual_statements=[annual, revised])
    assert [p["values"]["ps"] for p in result["points"]] == [1, 1, None]


def test_unverified_annual_filing_does_not_gate_an_earlier_quarter():
    annual = annual_statement()
    annual.filing_at = datetime(2024, 9, 30)
    annual.income_statement["totalRevenue"] = 1
    assert calculate(annual_statements=[annual])["points"][0]["values"]["ps"] == 1


def test_quarterly_quality_propagates_to_ttm_but_debt_uses_latest_balance_only():
    rows = statements()
    rows[0].income_statement.update(netIncome=-1, netIncomeApplicableToCommonShares=0)
    rows[0].balance_sheet.update(totalDebt=100, shortTermDebt=100)
    result = calculate(rows)
    assert result["points"][0]["values"]["pe"] is None
    assert "zero" in result["metrics"][0]["latest_reason"]
    assert result["points"][0]["values"]["ev_revenue"] == 1.2
    rows[-1].balance_sheet.update(totalDebt=100, shortTermDebt=100)
    result = calculate(rows)
    assert result["points"][0]["values"]["ev_revenue"] is None
    assert result["points"][0]["values"]["ps"] == 1


def test_real_small_earnings_keep_high_pe_and_sector_limits_explain_gaps():
    rows = statements()
    for row, net in zip(rows, [-29.9, 10, 10, 10]):
        row.income_statement["netIncome"] = net
    assert calculate(rows)["points"][0]["values"]["pe"] == pytest.approx(4000)
    utility = calculate(rows, sector="Utilities")
    assert utility["points"][0]["values"]["pe"] == pytest.approx(4000)
    assert utility["points"][0]["values"]["pfcf"] is None
    assert "capital-investment" in utility["metrics"][3]["latest_reason"]
    financial = calculate(sector="Financial Services")
    assert financial["points"][0]["values"]["pe"] is None
    assert "common shareholders" in financial["metrics"][0]["latest_reason"]


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_annual", [False, True])
async def test_database_loads_annual_versions_and_legacy_years(db_session, legacy_annual):
    from models import DailyPrice, FinancialStatement, FundamentalVersion, Ticker
    from services.split_history import persist_full_split_history
    from services.valuation_history import get_valuation_history

    db_session.add(Ticker(ticker="CHECK.US", currency="USD", sector="Technology"))
    await db_session.flush()
    db_session.add(DailyPrice(ticker="CHECK.US", date=date(2024, 11, 11), close=40))
    for row in statements():
        values = vars(row).copy()
        values.pop("raw_snapshot_id")
        db_session.add(FundamentalVersion(ticker="CHECK.US", period_type="Quarterly", **values))
    annual = annual_statement()
    annual.income_statement.update(totalRevenue=300, filing_date="2024-11-08")
    if legacy_annual:
        db_session.add(FinancialStatement(ticker="CHECK.US", fiscal_date=annual.period_end, period="Yearly",
            income_statement=annual.income_statement, balance_sheet=annual.balance_sheet, cash_flow=annual.cash_flow))
    else:
        values = vars(annual).copy()
        values.pop("raw_snapshot_id")
        db_session.add(FundamentalVersion(ticker="CHECK.US", **values))
    await persist_full_split_history(db_session, "CHECK.US", [], date(2025, 2, 1))
    await db_session.commit()
    result = await get_valuation_history("CHECK.US", db_session)
    assert result["points"][0]["values"]["ps"] is None
    assert "annual" in result["metrics"][1]["latest_reason"]
    assert result["points"][0]["values"]["pe"] == 10
