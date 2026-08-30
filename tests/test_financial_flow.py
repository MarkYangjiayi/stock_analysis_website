from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from core.config import settings
from core.time_utils import utc_now
from models import FinancialFlowRun, FinancialStatement, Ticker
from services.financial_flow import (
    build_consolidated_flow,
    enqueue_financial_flow,
    execute_financial_flow,
    extract_reported_detail,
    get_financial_flow,
    merge_reported_detail,
)


def statement(
    period_end: date = date(2026, 6, 30),
    *,
    ticker: str = "AMZN.US",
    period: str = "Quarterly",
    scale: float = 1.0,
    overrides: dict | None = None,
) -> FinancialStatement:
    income = {
        "totalRevenue": 200_606 * scale,
        "costOfRevenue": 95_778 * scale,
        "grossProfit": 104_828 * scale,
        "operatingIncome": 27_461 * scale,
        "incomeBeforeTax": 80_857 * scale,
        "incomeTaxExpense": 18_199 * scale,
        "netIncome": 62_647 * scale,
    }
    income.update(overrides or {})
    return FinancialStatement(
        ticker=ticker,
        fiscal_date=period_end,
        period=period,
        revenue=income.get("totalRevenue"),
        net_income=income.get("netIncome"),
        income_statement=income,
    )


def test_consolidated_flow_reconciles_and_flags_material_non_operating_income():
    previous = statement(
        date(2025, 6, 30),
        overrides={"totalRevenue": 167_172, "operatingIncome": 19_200, "netIncome": 18_600},
    )
    result = build_consolidated_flow(statement(), previous_statement=previous, currency="USD")

    assert result["chart_available"] is True
    assert result["validation"]["reconciled"] is True
    assert {card["key"] for card in result["summary_cards"]} == {"revenue", "operating_income", "net_income"}
    revenue_card = next(card for card in result["summary_cards"] if card["key"] == "revenue")
    assert revenue_card["yoy_change"] == pytest.approx(200_606 / 167_172 - 1)
    assert result["insights"][0]["code"] == "material_non_operating"
    assert sum(link["value"] for link in result["links"] if link["source"] == "revenue") == 200_606


def test_missing_values_remain_missing_and_do_not_become_zero():
    result = build_consolidated_flow(
        statement(overrides={"costOfRevenue": None, "grossProfit": None}),
        currency="USD",
    )

    assert result["chart_available"] is False
    assert {"cost_of_revenue", "gross_profit"}.issubset(result["validation"]["missing_fields"])
    assert all(node["id"] not in {"cost_of_revenue", "gross_profit"} for node in result["nodes"])


@pytest.mark.parametrize("field", ["grossProfit", "operatingIncome", "incomeBeforeTax", "netIncome"])
def test_negative_profit_stage_keeps_table_value_but_disables_sankey(field):
    result = build_consolidated_flow(statement(overrides={field: -1}), currency="USD")

    assert result["chart_available"] is False
    assert any(node["value"] < 0 for node in result["nodes"])
    assert result["links"] == []


def test_sec_tables_choose_quarter_not_ytd_and_convert_disclosed_millions():
    local = statement(scale=1_000_000)
    html = """
    <table>
      <tr><th>Line item</th><th>Three Months Ended June 30, 2026</th><th>Three Months Ended June 30, 2025</th><th>Six Months Ended June 30, 2026</th></tr>
      <tr><td>Total revenue</td><td>200,606</td><td>167,172</td><td>390,000</td></tr>
      <tr><td>Online stores</td><td>70,000</td><td>60,000</td><td>135,000</td></tr>
      <tr><td>Third-party seller services</td><td>46,000</td><td>39,000</td><td>90,000</td></tr>
      <tr><td>AWS</td><td>42,000</td><td>31,000</td><td>80,000</td></tr>
      <tr><td>Advertising services</td><td>19,000</td><td>16,000</td><td>37,000</td></tr>
      <tr><td>Subscription services</td><td>13,000</td><td>11,000</td><td>25,000</td></tr>
      <tr><td>Physical stores</td><td>5,800</td><td>5,000</td><td>11,000</td></tr>
      <tr><td>Other</td><td>4,806</td><td>5,172</td><td>12,000</td></tr>
      <tr><td>Cost of sales</td><td>95,778</td><td>84,000</td><td>187,000</td></tr>
      <tr><td>Technology &amp; infrastructure</td><td>33,158</td><td>28,000</td><td>65,000</td></tr>
      <tr><td>Fulfillment</td><td>29,633</td><td>25,000</td><td>58,000</td></tr>
      <tr><td>Sales &amp; marketing</td><td>11,698</td><td>10,000</td><td>23,000</td></tr>
      <tr><td>G&amp;A</td><td>2,788</td><td>2,400</td><td>5,400</td></tr>
      <tr><td>Other operating expense, net</td><td>90</td><td>75</td><td>180</td></tr>
    </table>
    """
    detail = extract_reported_detail([{
        "source_id": "SEC:AMZN:10-Q:2026Q2",
        "form": "10-Q",
        "filing_date": "2026-07-31",
        "source_url": "https://www.sec.gov/example",
        "html": html,
    }], local)
    result, validation = merge_reported_detail(
        build_consolidated_flow(local, currency="USD"),
        detail,
    )

    assert sum(row["value"] for row in detail["revenue_segments"]) == 200_606_000_000
    assert next(row for row in detail["revenue_segments"] if row["label"] == "AWS")["value"] == 42_000_000_000
    assert result["coverage_level"] == "full"
    assert validation["revenue_segments_reconciled"] is True
    assert validation["operating_expenses_reconciled"] is True
    assert all(node["source_id"].startswith("SEC:") for node in result["nodes"] if node["id"].startswith(("revenue_segment_", "expense_")))


def test_segment_gap_gets_labeled_reconciliation_but_incomplete_expenses_fall_back():
    local = statement()
    detail = {
        "revenue_segments": [
            {"label": "Business A", "value": 120_000, "source_id": "SEC:1", "original_label": "Business A", "disclosure_unit": 1},
            {"label": "Business B", "value": 75_000, "source_id": "SEC:1", "original_label": "Business B", "disclosure_unit": 1},
            {"label": "Other / reconciliation", "value": 5_606, "source_id": "SEC:1", "original_label": "Derived reconciliation", "derived": True},
        ],
        "operating_expenses": [
            {"id": "expense_marketing", "label": "Sales & marketing", "value": 11_698, "source_id": "SEC:1", "original_label": "Sales and marketing", "disclosure_unit": 1},
        ],
        "sources": [],
    }

    result, validation = merge_reported_detail(build_consolidated_flow(local, currency="USD"), detail)

    reconciliation = next(node for node in result["nodes"] if node["label"] == "Other / reconciliation")
    assert reconciliation["evidence_type"] == "derived_calculation"
    assert validation["operating_expenses_reconciled"] is False
    assert any(node["id"] == "operating_expenses" for node in result["nodes"])
    assert all(node["id"] != "expense_marketing" for node in result["nodes"])


def test_revenue_segments_are_reconciled_within_one_table():
    local = statement()
    html = """
    <table>
      <tr><th>Revenue</th><th>Three Months Ended June 30, 2026</th></tr>
      <tr><td>Product A revenue</td><td>110,000</td></tr>
      <tr><td>Product B revenue</td><td>50,000</td></tr>
    </table>
    <table>
      <tr><th>Revenue</th><th>Three Months Ended June 30, 2026</th></tr>
      <tr><td>Region A revenue</td><td>20,000</td></tr>
      <tr><td>Region B revenue</td><td>20,000</td></tr>
    </table>
    """
    detail = extract_reported_detail([{"source_id": "SEC:1", "form": "10-Q", "html": html}], local)

    assert detail["revenue_segments"] == []


@pytest.mark.asyncio
async def test_financial_company_returns_structured_unsupported_response(db_session, monkeypatch):
    monkeypatch.setattr(settings, "FINANCIAL_FLOW_ENRICHMENT_ENABLED", False)
    db_session.add(Ticker(ticker="BANK.US", name="Bank", sector="Financial Services", industry="Banks", currency="USD"))
    db_session.add(statement(ticker="BANK.US", period="Yearly"))
    await db_session.commit()

    result = await get_financial_flow("BANK.US", db_session, period_type="annual")

    assert result["status"] == "unsupported"
    assert result["coverage_level"] == "none"
    assert result["chart_available"] is False
    assert "industry-specific" in result["unsupported_reason"]


@pytest.mark.asyncio
async def test_failed_run_can_be_requeued_on_a_later_day(db_session):
    db_session.add(Ticker(ticker="AMZN.US", name="Amazon", sector="Consumer Cyclical", industry="Internet Retail", currency="USD"))
    local = statement(period="Yearly")
    db_session.add(local)
    await db_session.commit()
    run, created = await enqueue_financial_flow(db_session, local, "USD")
    assert created is True
    run.status = "failed"
    run.active_key = None
    run.created_at = utc_now() - timedelta(days=1)
    run.finished_at = utc_now() - timedelta(days=1)
    run.error_message = "temporary failure"
    await db_session.commit()

    retried, created = await enqueue_financial_flow(db_session, local, "USD")

    assert retried.id == run.id
    assert created is True
    assert retried.status == "queued"
    assert retried.error_message is None

    retried.status = "failed"
    retried.active_key = None
    retried.finished_at = utc_now()
    await db_session.commit()
    same_day, created = await enqueue_financial_flow(db_session, local, "USD")
    assert same_day.id == run.id
    assert created is False


@pytest.mark.asyncio
async def test_external_enrichment_stops_after_three_attempts(db_session, monkeypatch):
    import services.financial_flow as financial_flow

    db_session.add(Ticker(ticker="AMZN.US", name="Amazon", sector="Consumer Cyclical", industry="Internet Retail", currency="USD"))
    local = statement(period="Yearly")
    db_session.add(local)
    await db_session.commit()
    run, _ = await enqueue_financial_flow(db_session, local, "USD")

    async def cik(*_args, **_kwargs):
        return "1018724"

    async def no_sleep(_seconds):
        return None

    def fail_fetch(**_kwargs):
        raise RuntimeError("SEC unavailable")

    monkeypatch.setattr(financial_flow, "_resolve_cik", cik)
    monkeypatch.setattr(financial_flow, "_fetch_sec_documents_sync", fail_fetch)
    monkeypatch.setattr(financial_flow.asyncio, "sleep", no_sleep)

    await execute_financial_flow(run.id)
    await db_session.refresh(run)

    assert run.status == "failed"
    assert run.attempt_count == 3
    assert run.active_key is None
    assert run.global_slot is None


@pytest.mark.asyncio
async def test_cancellation_requeues_owned_run_and_duplicate_executor_cannot_claim(db_session, monkeypatch):
    import services.financial_flow as financial_flow

    db_session.add(Ticker(ticker="AMZN.US", name="Amazon", sector="Consumer Cyclical", industry="Internet Retail", currency="USD"))
    local = statement(period="Yearly")
    db_session.add(local)
    await db_session.commit()
    run, _ = await enqueue_financial_flow(db_session, local, "USD")

    started = asyncio.Event()
    released = asyncio.Event()

    async def cik(*_args, **_kwargs):
        return "1018724"

    async def blocked_to_thread(*_args, **_kwargs):
        started.set()
        await released.wait()

    monkeypatch.setattr(financial_flow, "_resolve_cik", cik)
    monkeypatch.setattr(financial_flow.asyncio, "to_thread", blocked_to_thread)
    monkeypatch.setattr(financial_flow, "_semaphore", asyncio.Semaphore(2))

    owner = asyncio.create_task(execute_financial_flow(run.id))
    await started.wait()
    duplicate = asyncio.create_task(execute_financial_flow(run.id))
    await duplicate
    owner.cancel()
    released.set()
    with pytest.raises(asyncio.CancelledError):
        await owner
    await db_session.refresh(run)

    assert run.status == "queued"
    assert run.stage == "interrupted"
    assert run.active_key is not None
    assert run.owner_token is None
    assert run.lease_expires_at is None


@pytest.mark.asyncio
async def test_financial_flow_api_defaults_to_latest_and_serves_history(db_session, monkeypatch):
    monkeypatch.setattr(settings, "FINANCIAL_FLOW_ENRICHMENT_ENABLED", False)
    db_session.add(Ticker(ticker="AMZN.US", name="Amazon", sector="Consumer Cyclical", industry="Internet Retail", currency="USD"))
    db_session.add(statement(date(2026, 3, 31)))
    db_session.add(statement(date(2026, 6, 30)))
    await db_session.commit()

    from main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        latest = await client.get("/api/stocks/AMZN.US/financial-flow", params={"period_type": "quarterly"})
        history = await client.get("/api/stocks/AMZN.US/financial-flow", params={"period_type": "quarterly", "period_end": "2026-03-31"})
        missing = await client.get("/api/stocks/AMZN.US/financial-flow", params={"period_type": "quarterly", "period_end": "2024-03-31"})
        invalid = await client.get("/api/stocks/AMZN.US/financial-flow", params={"period_type": "ttm"})

    assert latest.status_code == 200
    assert latest.json()["period_end"] == "2026-06-30"
    assert latest.json()["available_periods"] == ["2026-06-30", "2026-03-31"]
    assert next(card for card in latest.json()["summary_cards"] if card["key"] == "revenue")["yoy_change"] is None
    assert history.status_code == 200
    assert history.json()["period_end"] == "2026-03-31"
    assert missing.status_code == 404
    assert invalid.status_code == 422
