import os
import sqlite3
import subprocess
import sys
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from models import (
    DailyPrice,
    DataPublication,
    DecisionBriefCache,
    FactorValue,
    FinancialStatement,
    PipelineRun,
    StockScreenerSnapshot,
    Ticker,
)
from services.ai_assistant import (
    EvidenceCitationError,
    build_evidence_hash,
    generate_stock_report,
    validate_evidence_citations,
    validate_evidence_numbers,
)
from services.decision_support import (
    DEFAULT_SCENARIOS,
    build_financial_context,
    build_peer_comparison,
    calculate_dcf_value,
    calculate_valuation,
    evaluate_fundamental_warnings,
    get_decision_support,
    midrank_percentile,
    validate_scenarios,
)
from services.analyzer import get_analyzed_stock_data


def _scenario_copy():
    return deepcopy(DEFAULT_SCENARIOS)


def test_dcf_formula_and_base_sensitivity_cell_are_transparent():
    inputs = {"fcf": 100.0, "cash": 50.0, "debt": 20.0, "shares": 10.0}
    valuation = calculate_valuation(inputs, DEFAULT_SCENARIOS, current_price=200.0)
    base = valuation["scenarios"][1]

    projected = [100 * (1.10 ** year) for year in range(1, 6)]
    explicit = sum(value / (1.09 ** year) for year, value in enumerate(projected, 1))
    terminal = projected[-1] * 1.025 / (0.09 - 0.025)
    expected = (explicit + terminal / (1.09 ** 5) + 50 - 20) / 10

    assert base["intrinsic_value_per_share"] == pytest.approx(expected)
    assert valuation["sensitivity"]["values"][2][2] == pytest.approx(expected)
    assert len(valuation["sensitivity"]["values"]) == 5
    assert all(len(row) == 5 for row in valuation["sensitivity"]["values"])
    assert valuation["formula"]["cash_treatment"] == "added"
    assert valuation["formula"]["debt_treatment"] == "deducted"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda rows: rows[0].update(fcf_growth_rate=-0.201), "between -20% and 50%"),
        (lambda rows: rows[2].update(fcf_growth_rate=0.501), "between -20% and 50%"),
        (lambda rows: rows[2].update(wacc=0.029), "between 3% and 25%"),
        (lambda rows: rows[0].update(wacc=0.251), "between 3% and 25%"),
        (lambda rows: rows[0].update(perpetual_growth=-0.021), "between -2% and 6%"),
        (lambda rows: rows[2].update(perpetual_growth=0.061), "between -2% and 6%"),
        (lambda rows: rows[1].update(wacc=0.03, perpetual_growth=0.026), "at least 0.5"),
        (lambda rows: rows[0].update(fcf_growth_rate=0.11), "Bear <= Base <= Bull"),
        (lambda rows: rows[0].update(wacc=0.085), "Bear >= Base >= Bull"),
    ],
)
def test_scenario_validation_rejects_invalid_bounds_and_order(mutate, message):
    scenarios = _scenario_copy()
    mutate(scenarios)
    with pytest.raises(ValueError, match=message):
        validate_scenarios(scenarios)


def test_scenario_validation_accepts_inclusive_bounds_when_ordered():
    scenarios = [
        {"scenario": "bear", "fcf_growth_rate": -0.20, "wacc": 0.25, "perpetual_growth": -0.02},
        {"scenario": "base", "fcf_growth_rate": 0.10, "wacc": 0.10, "perpetual_growth": 0.02},
        {"scenario": "bull", "fcf_growth_rate": 0.50, "wacc": 0.065, "perpetual_growth": 0.06},
    ]
    assert validate_scenarios(scenarios) == scenarios


@pytest.mark.parametrize(
    ("inputs", "reason"),
    [
        ({"fcf": None, "cash": 1, "debt": 1, "shares": 1}, "Free cash flow is unavailable"),
        ({"fcf": 1, "cash": None, "debt": 1, "shares": 1}, "Cash and short-term investments are unavailable"),
        ({"fcf": 1, "cash": 1, "debt": None, "shares": 1}, "Total debt is unavailable"),
        ({"fcf": 1, "cash": 1, "debt": 1, "shares": None}, "Shares outstanding are unavailable"),
        ({"fcf": 0, "cash": 1, "debt": 1, "shares": 1}, "Free cash flow must be positive"),
        ({"fcf": -1, "cash": 1, "debt": 1, "shares": 1}, "Free cash flow must be positive"),
        ({"fcf": 1, "cash": 1, "debt": 1, "shares": 0}, "Shares outstanding must be positive"),
    ],
)
def test_valuation_returns_precise_unavailable_reasons(inputs, reason):
    valuation = calculate_valuation(inputs, DEFAULT_SCENARIOS, current_price=10)
    assert valuation["available"] is False
    assert any(reason in item for item in valuation["unavailable_reasons"])
    assert valuation["scenarios"][1]["available"] is False
    assert valuation["scenarios"][1].get("intrinsic_value_per_share") is None


def test_midrank_percentile_handles_ties():
    assert midrank_percentile(2, [1, 2, 2, 3]) == pytest.approx(50)


def _peer_row(index: int, *, industry: str = "Software", sector: str = "Technology"):
    return StockScreenerSnapshot(
        ticker=f"P{index:02d}.US",
        date=date(2025, 6, 30),
        industry=industry,
        sector=sector,
        pe_ratio=index + 1,
        forward_pe=index + 2,
        peg_ratio=index / 10 + 0.5,
        ps_ratio=index / 10 + 1,
        pb_ratio=index / 10 + 1,
        price_fcf=index + 3,
        ev_ebitda=index + 4,
        debt_to_equity=index / 10,
        sales_growth_ttm=index / 100,
        sales_growth_3yr=index / 100,
        sales_growth_5yr=index / 100,
        eps_growth_ttm=index / 100,
        eps_growth_3yr=index / 100,
        eps_growth_5yr=index / 100,
        gross_margin=index / 100,
        operating_margin=index / 100,
        net_profit_margin=index / 100,
        roe=index / 100,
        roa=index / 100,
        roic=index / 100,
    )


def test_peer_percentiles_apply_direction_and_industry_threshold():
    rows = [_peer_row(index) for index in range(25)]
    target = rows[4]
    result = build_peer_comparison(target, rows)
    pe = next(item for item in result["metrics"] if item["key"] == "pe_ratio")
    growth = next(item for item in result["metrics"] if item["key"] == "sales_growth_ttm")

    assert pe["industry"]["available"] is True
    assert pe["summary_scope"] == "industry"
    assert pe["industry"]["desirability_percentile"] == pytest.approx(
        100 - pe["industry"]["raw_percentile"]
    )
    assert growth["industry"]["desirability_percentile"] == pytest.approx(
        growth["industry"]["raw_percentile"]
    )


def test_peer_invalid_multiples_are_excluded_and_sector_fallback_is_used():
    rows = [_peer_row(index, industry="Target industry" if index < 9 else "Other") for index in range(25)]
    target = rows[4]
    rows[0].pe_ratio = 0
    rows[1].pe_ratio = -2
    rows[2].debt_to_equity = -1
    result = build_peer_comparison(target, rows)
    pe = next(item for item in result["metrics"] if item["key"] == "pe_ratio")
    debt = next(item for item in result["metrics"] if item["key"] == "debt_to_equity")

    assert pe["industry"]["available"] is False
    assert pe["industry"]["observation_count"] == 7
    assert pe["sector"]["observation_count"] == 23
    assert pe["summary_scope"] == "sector"
    assert debt["sector"]["observation_count"] == 24


def _warning_context():
    return {
        "current_ttm": {
            "revenue": 100.0,
            "gross_margin": 0.40,
            "operating_margin": 0.20,
            "fcf": 100.0,
            "net_income": 100.0,
        },
        "previous_ttm": {
            "revenue": 100.0,
            "gross_margin": 0.40,
            "operating_margin": 0.20,
            "fcf": 100.0,
            "net_income": 100.0,
        },
        "latest_balance": {
            "cash": 100.0,
            "cash_and_short_term_investments": 100.0,
            "debt_to_equity": 0.4,
            "shares": 100.0,
        },
        "prior_year_balance": {
            "cash": 100.0,
            "cash_and_short_term_investments": 100.0,
            "debt_to_equity": 0.4,
            "shares": 100.0,
        },
        "data_quality_notes": [],
    }


def _warning(context, rule_id):
    return next(
        (item for item in evaluate_fundamental_warnings(context)["warnings"] if item["id"] == rule_id),
        None,
    )


@pytest.mark.parametrize(
    ("decline", "severity"),
    [(0.0499, None), (0.05, "warning"), (0.15, "high")],
)
def test_revenue_warning_thresholds(decline, severity):
    context = _warning_context()
    context["current_ttm"]["revenue"] = 100 * (1 - decline)
    result = _warning(context, "revenue_decline")
    assert (result or {}).get("severity") == severity


@pytest.mark.parametrize("rule_id,key", [("gross_margin_compression", "gross_margin"), ("operating_margin_compression", "operating_margin")])
@pytest.mark.parametrize(("compression", "severity"), [(0.0299, None), (0.03, "warning"), (0.08, "high")])
def test_margin_warning_thresholds(rule_id, key, compression, severity):
    context = _warning_context()
    context["current_ttm"][key] = context["previous_ttm"][key] - compression
    result = _warning(context, rule_id)
    assert (result or {}).get("severity") == severity


@pytest.mark.parametrize(("decline", "severity"), [(0.2999, None), (0.30, "warning"), (0.60, "high")])
def test_fcf_decline_thresholds(decline, severity):
    context = _warning_context()
    context["current_ttm"]["fcf"] = 100 * (1 - decline)
    context["current_ttm"]["net_income"] = context["current_ttm"]["fcf"]
    result = _warning(context, "fcf_decline")
    assert (result or {}).get("severity") == severity


def test_negative_fcf_is_high():
    context = _warning_context()
    context["current_ttm"]["fcf"] = -1
    assert _warning(context, "fcf_decline")["severity"] == "high"


@pytest.mark.parametrize(("conversion", "severity"), [(0.50, None), (0.499, "warning"), (0.25, "warning"), (0.249, "high")])
def test_fcf_conversion_thresholds(conversion, severity):
    context = _warning_context()
    context["current_ttm"]["fcf"] = 100 * conversion
    result = _warning(context, "fcf_conversion")
    assert (result or {}).get("severity") == severity


@pytest.mark.parametrize(("debt_to_equity", "severity"), [(1.5, None), (1.5001, "warning"), (3.0, "warning"), (3.0001, "high")])
def test_debt_level_thresholds_are_strictly_above(debt_to_equity, severity):
    context = _warning_context()
    context["latest_balance"]["debt_to_equity"] = debt_to_equity
    context["prior_year_balance"]["debt_to_equity"] = debt_to_equity
    result = _warning(context, "debt_level")
    assert (result or {}).get("severity") == severity


def test_debt_growth_requires_more_than_50_percent_and_current_above_half():
    context = _warning_context()
    context["prior_year_balance"]["debt_to_equity"] = 0.4
    context["latest_balance"]["debt_to_equity"] = 0.6
    assert _warning(context, "debt_increase") is None
    context["latest_balance"]["debt_to_equity"] = 0.601
    assert _warning(context, "debt_increase")["severity"] == "warning"


@pytest.mark.parametrize(("decline", "severity"), [(0.2499, None), (0.25, "warning"), (0.50, "high")])
def test_cash_decline_thresholds(decline, severity):
    context = _warning_context()
    context["latest_balance"]["cash_and_short_term_investments"] = 100 * (1 - decline)
    result = _warning(context, "cash_decline")
    assert (result or {}).get("severity") == severity


@pytest.mark.parametrize(("increase", "severity"), [(0.0299, None), (0.03, "warning"), (0.10, "high")])
def test_share_dilution_thresholds(increase, severity):
    context = _warning_context()
    context["latest_balance"]["shares"] = 100 * (1 + increase)
    result = _warning(context, "share_dilution")
    assert (result or {}).get("severity") == severity


def test_missing_and_negative_comparison_periods_are_notes_not_warnings():
    context = _warning_context()
    context["previous_ttm"]["revenue"] = -10
    context["previous_ttm"]["fcf"] = -10
    context["prior_year_balance"]["cash_and_short_term_investments"] = None
    result = evaluate_fundamental_warnings(context)
    assert _warning(context, "revenue_decline") is None
    assert _warning(context, "fcf_decline") is None
    codes = {item["code"] for item in result["data_quality_notes"]}
    assert {"revenue_comparison_unavailable", "fcf_comparison_unavailable", "cash_comparison_unavailable"} <= codes


def _quarterly_statement(ticker: str, fiscal_date: date, scale: float = 1.0, *, fcf: float = 25.0):
    return FinancialStatement(
        ticker=ticker,
        fiscal_date=fiscal_date,
        period="Quarterly",
        revenue=100 * scale,
        net_income=20 * scale,
        income_statement={
            "totalRevenue": 100 * scale,
            "grossProfit": 50 * scale,
            "operatingIncome": 25 * scale,
            "netIncome": 20 * scale,
        },
        cash_flow={"freeCashFlow": fcf * scale},
        balance_sheet={
            "cashAndShortTermInvestments": 200 * scale,
            "totalDebt": 50 * scale,
            "totalStockholderEquity": 250 * scale,
            "commonStockSharesOutstanding": 100,
        },
    )


def test_prior_ttm_must_immediately_precede_current_ttm():
    records = [
        _quarterly_statement("AAA.US", value)
        for value in (
            date(2025, 12, 31), date(2025, 9, 30), date(2025, 6, 30), date(2025, 3, 31),
            date(2023, 12, 31), date(2023, 9, 30), date(2023, 6, 30), date(2023, 3, 31),
        )
    ]
    context = build_financial_context(records)
    assert context["current_ttm"]["revenue"] == pytest.approx(400)
    assert context["previous_ttm"]["revenue"] is None
    assert any(note["code"] == "non_contiguous_previous_ttm" for note in context["data_quality_notes"])


@pytest.mark.asyncio
async def test_financial_history_exposes_optional_warning_evidence_fields(db_session):
    db_session.add(Ticker(ticker="HISTORY.US", name="History"))
    db_session.add(FinancialStatement(
        ticker="HISTORY.US",
        fiscal_date=date(2025, 12, 31),
        period="Yearly",
        revenue=100,
        net_income=20,
        income_statement={"totalRevenue": 100, "grossProfit": 50, "operatingIncome": 25, "netIncome": 20},
        cash_flow={"freeCashFlow": 18},
        balance_sheet={
            "cashAndShortTermInvestments": 40,
            "totalDebt": 10,
            "commonStockSharesOutstanding": 5,
        },
    ))
    await db_session.commit()
    result = await get_analyzed_stock_data("HISTORY.US", db_session)
    point = result["historical_financials"][0]
    assert point["free_cash_flow"] == pytest.approx(18)
    assert point["operating_margin"] == pytest.approx(0.25)
    assert point["cash_and_short_term_investments"] == pytest.approx(40)
    assert point["total_debt"] == pytest.approx(10)
    assert point["shares_outstanding"] == pytest.approx(5)


@pytest.mark.asyncio
async def test_complete_sparse_outside_and_negative_fcf_decision_fixtures(db_session):
    tickers = ["COMPLETE.US", "SPARSE.US", "OUTSIDE.US", "NEGFCF.US"]
    db_session.add_all([Ticker(ticker=ticker, name=ticker, sector="Technology", industry="Software") for ticker in tickers])
    db_session.add_all([
        DailyPrice(ticker=ticker, date=date(2025, 12, 31), close=100, adjusted_close=100)
        for ticker in tickers
    ])
    quarter_dates = [
        date(2025, 12, 31), date(2025, 9, 30), date(2025, 6, 30), date(2025, 3, 31),
        date(2024, 12, 31), date(2024, 9, 30), date(2024, 6, 30), date(2024, 3, 31),
    ]
    db_session.add_all([_quarterly_statement("COMPLETE.US", value) for value in quarter_dates])
    db_session.add_all([_quarterly_statement("SPARSE.US", value) for value in quarter_dates[:2]])
    db_session.add_all([_quarterly_statement("OUTSIDE.US", value) for value in quarter_dates])
    db_session.add_all([_quarterly_statement("NEGFCF.US", value, fcf=-10) for value in quarter_dates])

    run = PipelineRun(pipeline_name="screener", target_date=date(2025, 12, 31), status="published")
    factor_run = PipelineRun(pipeline_name="factors", target_date=date(2025, 12, 31), status="published")
    db_session.add_all([run, factor_run])
    await db_session.flush()
    db_session.add_all([
        DataPublication(dataset="screener", as_of_date=date(2025, 12, 31), pipeline_run_id=run.id, status="published"),
        DataPublication(dataset="factors", as_of_date=date(2025, 12, 31), pipeline_run_id=factor_run.id, status="published"),
    ])
    peer_rows = [_peer_row(index) for index in range(25)]
    for row in peer_rows:
        row.date = date(2025, 12, 31)
    complete = _peer_row(30)
    complete.ticker = "COMPLETE.US"
    complete.date = date(2025, 12, 31)
    negative = _peer_row(31)
    negative.ticker = "NEGFCF.US"
    negative.date = date(2025, 12, 31)
    db_session.add_all([*peer_rows, complete, negative])
    db_session.add(FactorValue(ticker="COMPLETE.US", as_of_date=date(2025, 12, 31), factor_name="quality", normalized_value=1.2, version="v1", available_at=datetime(2026, 1, 1), source_run_id=factor_run.id))
    await db_session.commit()

    complete_result = await get_decision_support("COMPLETE", db_session)
    sparse_result = await get_decision_support("SPARSE", db_session)
    outside_result = await get_decision_support("OUTSIDE", db_session)
    negative_result = await get_decision_support("NEGFCF", db_session)

    assert complete_result["valuation"]["available"] is True
    assert complete_result["summary"]["coverage"]["quarterly_statements"] == 8
    assert len(complete_result["evidence"]) == 36
    assert sparse_result["valuation"]["available"] is False
    assert sparse_result["risks"]["warnings"] == []
    assert outside_result["peer_comparison"]["ticker_in_screener"] is False
    assert "outside the latest published Screener universe" in " ".join(outside_result["summary"]["coverage"]["missing_data_reasons"])
    assert negative_result["valuation"]["available"] is False
    assert any(item["severity"] == "high" and item["id"] == "fcf_decline" for item in negative_result["risks"]["warnings"])


def test_personal_endpoints_require_key_and_watchlist_import_is_idempotent():
    from main import app

    with TestClient(app) as client:
        assert client.get("/api/personal/watchlist").status_code == 401
        assert client.get("/api/personal/watchlist", headers={"X-API-Key": "wrong"}).status_code == 401
        assert client.get("/api/personal/watchlist", headers={"X-API-Key": "test-secret"}).json() == {"tickers": []}

        imported = client.post(
            "/api/personal/watchlist/import",
            headers={"X-API-Key": "test-secret"},
            json={"tickers": ["AAPL", "NVDA", "AAPL.US"]},
        )
        assert imported.status_code == 200
        assert imported.json() == {"tickers": ["AAPL.US", "NVDA.US"], "imported": True}

        second = client.post(
            "/api/personal/watchlist/import",
            headers={"X-API-Key": "test-secret"},
            json={"tickers": ["TSLA"]},
        )
        assert second.json() == {"tickers": ["AAPL.US", "NVDA.US"], "imported": False}

        replaced = client.put(
            "/api/personal/watchlist",
            headers={"X-API-Key": "test-secret"},
            json={"tickers": ["MSFT", "AAPL"]},
        )
        assert replaced.json() == {"tickers": ["MSFT.US", "AAPL.US"]}


def test_personal_valuation_endpoints_save_and_reset_scenarios():
    from main import app

    scenarios = _scenario_copy()
    scenarios[1]["fcf_growth_rate"] = 0.11
    with TestClient(app) as client:
        missing = client.put("/api/personal/stocks/AAPL/valuation-scenarios", json={"scenarios": scenarios})
        assert missing.status_code == 401
        saved = client.put(
            "/api/personal/stocks/AAPL/valuation-scenarios",
            headers={"X-API-Key": "test-secret"},
            json={"scenarios": scenarios},
        )
        assert saved.status_code == 200
        assert saved.json()["is_saved"] is True
        read = client.get(
            "/api/personal/stocks/AAPL/valuation-scenarios",
            headers={"X-API-Key": "test-secret"},
        )
        assert read.json()["scenarios"][1]["fcf_growth_rate"] == pytest.approx(0.11)
        reset = client.delete(
            "/api/personal/stocks/AAPL/valuation-scenarios",
            headers={"X-API-Key": "test-secret"},
        )
        assert reset.json()["is_saved"] is False
        assert reset.json()["scenarios"] == DEFAULT_SCENARIOS


def test_public_decision_support_never_exposes_saved_personal_scenarios():
    from main import app

    scenarios = _scenario_copy()
    scenarios[1]["fcf_growth_rate"] = 0.11
    with TestClient(app) as client:
        saved = client.put(
            "/api/personal/stocks/AAPL/valuation-scenarios",
            headers={"X-API-Key": "test-secret"},
            json={"scenarios": scenarios},
        )
        assert saved.status_code == 200

        public = client.get("/api/stocks/AAPL/decision-support")
        personal = client.get(
            "/api/stocks/AAPL/decision-support",
            headers={"X-API-Key": "test-secret"},
        )
        invalid = client.get(
            "/api/stocks/AAPL/decision-support",
            headers={"X-API-Key": "wrong"},
        )

        assert public.status_code == 200
        assert public.json()["valuation"]["scenario_source"] == "default"
        assert public.json()["valuation"]["scenarios"][1]["assumptions"]["fcf_growth_rate"] == pytest.approx(0.10)
        assert personal.json()["valuation"]["scenario_source"] == "saved"
        assert personal.json()["valuation"]["scenarios"][1]["assumptions"]["fcf_growth_rate"] == pytest.approx(0.11)
        assert invalid.status_code == 401


def _valid_brief():
    return """## Core View
The snapshot is available [E1].

## Valuation
The base case is evidence-bound [E5].

## Peer Context
Peer context uses the published cross-section [E7].

## Risks
The deterministic risk record is cited [E27]."""


def _decision_for_ai():
    return {
        "metadata": {
            "ticker": "AAA.US",
            "company_name": "AAA Corporation",
            "price_date": "2025-01-01",
        },
        "valuation": {"scenarios": [{"assumptions": DEFAULT_SCENARIOS[0]}]},
        "evidence": [
            {"id": "E1", "available": True, "source_date": "2025-01-01", "value": 10},
            {"id": "E5", "available": True, "source_date": "2025-01-01", "value": 12},
            {"id": "E7", "available": True, "source_date": "2025-01-01", "value": 80},
            {"id": "E27", "available": True, "source_date": "2025-01-01", "value": {"triggered": False}},
        ],
    }


def test_ai_citation_validation_rejects_missing_and_unknown_ids():
    allowed = {"E1", "E5", "E7", "E27"}
    validate_evidence_citations(_valid_brief(), allowed)
    with pytest.raises(EvidenceCitationError, match="Unknown"):
        validate_evidence_citations(_valid_brief().replace("[E27]", "[E999]"), allowed)
    with pytest.raises(EvidenceCitationError, match="Risks section"):
        validate_evidence_citations(_valid_brief().replace(" [E27]", ""), allowed)


def test_ai_numeric_validation_accepts_only_numbers_supported_by_cited_evidence():
    evidence = [
        {
            "id": "E3",
            "label": "Quarterly financial coverage",
            "source_date": "2026-06-30",
            "value": {
                "statement_count": 8,
                "revenue": 466_823_000_000,
                "gross_margin": 0.486529,
            },
        },
        {
            "id": "E5",
            "label": "Base DCF",
            "source_date": "2026-06-30",
            "value": {"intrinsic_value_per_share": 209.067, "upside_downside": -0.3242386},
        },
    ]
    valid = (
        "As of 2026-06-30, 8 statements show revenue of $466.8 billion "
        "and gross margin of 48.7% [E3].\n"
        "The base case is $209.07 per share, or 32.4% below the current price [E5]."
    )
    validate_evidence_numbers(valid, evidence)

    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim '24%'"):
        validate_evidence_numbers(valid.replace("32.4%", "24%"), evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported date claim"):
        validate_evidence_numbers(valid.replace("2026-06-30", "2026-06-29", 1), evidence)
    with pytest.raises(EvidenceCitationError, match="must cite supporting evidence"):
        validate_evidence_numbers("Revenue was $466.8 billion.", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("The base case has +32.4% upside [E5].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("The base case has 32.4% upside [E5].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("The base case differs by 32.4% [E5].", evidence)


def test_ai_evidence_hash_changes_for_values_assumptions_dates_and_model():
    decision = _decision_for_ai()
    baseline = build_evidence_hash(decision, "model-a")
    changed_value = deepcopy(decision)
    changed_value["evidence"][0]["value"] = 11
    changed_date = deepcopy(decision)
    changed_date["metadata"]["price_date"] = "2025-01-02"
    changed_assumption = deepcopy(decision)
    changed_assumption["valuation"]["scenarios"][0]["assumptions"]["wacc"] = 0.2
    changed_identity = deepcopy(decision)
    changed_identity["metadata"]["company_name"] = "AAA Holdings"
    assert len({
        baseline,
        build_evidence_hash(changed_value, "model-a"),
        build_evidence_hash(changed_date, "model-a"),
        build_evidence_hash(changed_assumption, "model-a"),
        build_evidence_hash(changed_identity, "model-a"),
        build_evidence_hash(decision, "model-b"),
    }) == 6


@pytest.mark.asyncio
async def test_ai_validated_response_is_cached_and_cache_hits_skip_generation(db_session, monkeypatch):
    decision = _decision_for_ai()
    monkeypatch.setattr("services.ai_assistant.settings.DEEPSEEK_API_KEY", "configured")
    monkeypatch.setattr("services.ai_assistant.settings.DEEPSEEK_MODEL", "test-model")
    calls = 0

    async def generate(_prompt):
        nonlocal calls
        calls += 1
        return _valid_brief()

    monkeypatch.setattr("services.ai_assistant.generate_deepseek_text", generate)
    first = "".join([chunk async for chunk in generate_stock_report("AAA.US", decision, db_session)])
    second = "".join([chunk async for chunk in generate_stock_report("AAA.US", decision, db_session)])
    count = (await db_session.execute(select(func.count(DecisionBriefCache.id)))).scalar_one()
    assert first == _valid_brief()
    assert second == first
    assert calls == 1
    assert count == 1


@pytest.mark.asyncio
async def test_ai_invalid_citations_are_not_cached(db_session, monkeypatch):
    decision = _decision_for_ai()
    monkeypatch.setattr("services.ai_assistant.settings.DEEPSEEK_API_KEY", "configured")
    monkeypatch.setattr("services.ai_assistant.settings.DEEPSEEK_MODEL", "test-model")

    async def generate(_prompt):
        return _valid_brief().replace("[E27]", "[E999]")

    monkeypatch.setattr("services.ai_assistant.generate_deepseek_text", generate)
    response = "".join([chunk async for chunk in generate_stock_report("AAA.US", decision, db_session)])
    count = (await db_session.execute(select(func.count(DecisionBriefCache.id)))).scalar_one()
    assert response.startswith("Error:")
    assert count == 0


@pytest.mark.asyncio
async def test_ai_unsupported_numbers_are_not_cached(db_session, monkeypatch):
    decision = _decision_for_ai()
    monkeypatch.setattr("services.ai_assistant.settings.DEEPSEEK_API_KEY", "configured")
    monkeypatch.setattr("services.ai_assistant.settings.DEEPSEEK_MODEL", "test-model")

    async def generate(_prompt):
        return _valid_brief().replace(
            "The base case is evidence-bound [E5].",
            "The base case downside is 24% [E5].",
        )

    monkeypatch.setattr("services.ai_assistant.generate_deepseek_text", generate)
    response = "".join([chunk async for chunk in generate_stock_report("AAA.US", decision, db_session)])
    count = (await db_session.execute(select(func.count(DecisionBriefCache.id)))).scalar_one()
    assert response.startswith("Error:")
    assert count == 0


@pytest.mark.asyncio
async def test_ai_validation_failure_gets_one_repair_attempt(db_session, monkeypatch):
    decision = _decision_for_ai()
    monkeypatch.setattr("services.ai_assistant.settings.DEEPSEEK_API_KEY", "configured")
    monkeypatch.setattr("services.ai_assistant.settings.DEEPSEEK_MODEL", "test-model")
    calls = 0

    async def generate(prompt):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _valid_brief().replace(
                "The base case is evidence-bound [E5].",
                "The base case downside is 24% [E5].",
            )
        assert "Unsupported numeric claim '24%'" in prompt
        return _valid_brief()

    monkeypatch.setattr("services.ai_assistant.generate_deepseek_text", generate)
    response = "".join([chunk async for chunk in generate_stock_report("AAA.US", decision, db_session)])
    count = (await db_session.execute(select(func.count(DecisionBriefCache.id)))).scalar_one()
    assert response == _valid_brief()
    assert calls == 2
    assert count == 1


def test_report_cache_hit_bypasses_expensive_request_limit(monkeypatch):
    from api import routers
    from main import app

    limiter_calls = 0

    async def decision(*args, **kwargs):
        return _decision_for_ai()

    async def cached(*args, **kwargs):
        return _valid_brief()

    async def limit(*args, **kwargs):
        nonlocal limiter_calls
        limiter_calls += 1

    monkeypatch.setattr(routers, "get_decision_support", decision)
    monkeypatch.setattr(routers, "get_cached_stock_report", cached)
    monkeypatch.setattr(routers, "limit_expensive_requests", limit)

    with TestClient(app) as client:
        response = client.get("/api/stocks/AAA.US/report")

    assert response.status_code == 200
    assert response.text == _valid_brief()
    assert limiter_calls == 0


def test_report_cache_miss_consumes_expensive_request_limit(monkeypatch):
    from api import routers
    from main import app

    limiter_calls = 0

    async def decision(*args, **kwargs):
        return _decision_for_ai()

    async def cached(*args, **kwargs):
        return None

    async def limit(*args, **kwargs):
        nonlocal limiter_calls
        limiter_calls += 1

    async def generate(*args, **kwargs):
        yield _valid_brief()

    monkeypatch.setattr(routers, "get_decision_support", decision)
    monkeypatch.setattr(routers, "get_cached_stock_report", cached)
    monkeypatch.setattr(routers, "limit_expensive_requests", limit)
    monkeypatch.setattr(routers, "generate_stock_report", generate)

    with TestClient(app) as client:
        response = client.get("/api/stocks/AAA.US/report")

    assert response.status_code == 200
    assert response.text == _valid_brief()
    assert limiter_calls == 1


def test_personal_decision_support_migration_up_and_down(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "decision-migration.db"
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{database_path}",
        "ENVIRONMENT": "test",
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=project_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    expected = {"personal_watchlist_items", "ticker_valuation_scenarios", "decision_brief_cache"}
    with sqlite3.connect(database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert expected <= tables

    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0008_backfill_live_universe_source"],
        cwd=project_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    with sqlite3.connect(database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not expected & tables

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=project_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    with sqlite3.connect(database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert expected <= tables
