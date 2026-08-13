import asyncio
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
    CorporateAction,
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
    _company_identity_strings,
    _validation_safe_repair_template,
    build_evidence_hash,
    generate_stock_report,
    validate_evidence_citations,
    validate_evidence_numbers,
    validate_evidence_qualitative_claims,
)
from services.decision_support import (
    DEFAULT_SCENARIOS,
    _earnings_analysis_evidence,
    build_financial_context,
    build_peer_comparison,
    build_peer_multiple_distribution,
    calculate_dcf_value,
    calculate_valuation,
    evaluate_fundamental_warnings,
    get_decision_support,
    get_peer_multiple_distribution,
    midrank_percentile,
    validate_scenarios,
)
from services.analyzer import get_analyzed_stock_data
from services.data_sync import sync_ticker_data


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
        name=f"Peer {index:02d}",
        date=date(2025, 6, 30),
        industry=industry,
        sector=sector,
        market_cap=1_000_000_000 * (index + 1),
        pe_ratio=index + 1,
        forward_pe=index + 2,
        peg_ratio=index / 10 + 0.5,
        ps_ratio=index / 10 + 1,
        pb_ratio=index / 10 + 1,
        price_fcf=index + 3,
        ev_sales=index / 10 + 1.5,
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


def test_peer_multiple_distribution_excludes_target_and_calculates_statistics():
    rows = [_peer_row(index) for index in range(11)]
    target = rows[5]
    result = build_peer_multiple_distribution(
        target,
        rows,
        ticker=target.ticker,
        metric_key="ps_ratio",
        as_of_date=target.date,
    )

    expected_values = sorted(float(row.ps_ratio) for row in rows if row is not target)
    assert result["available"] is True
    assert result["cohort"]["scope"] == "industry"
    assert result["cohort"]["valid_count"] == 10
    assert result["distribution"]["mean"] == pytest.approx(sum(expected_values) / 10)
    assert result["distribution"]["median"] == pytest.approx(1.5)
    assert result["target"]["raw_percentile"] == pytest.approx(50)
    assert result["target"]["premium_to_median"] == pytest.approx(0)
    assert target.ticker not in {peer["ticker"] for peer in result["peers"]}


def test_peer_multiple_distribution_auto_fallback_and_explicit_scope():
    rows = [
        _peer_row(index, industry="Target" if index < 10 else "Other")
        for index in range(25)
    ]
    target = rows[4]
    automatic = build_peer_multiple_distribution(
        target,
        rows,
        ticker=target.ticker,
        metric_key="ev_sales",
    )
    explicit = build_peer_multiple_distribution(
        target,
        rows,
        ticker=target.ticker,
        metric_key="ev_sales",
        scope="industry",
    )

    assert automatic["available"] is True
    assert automatic["cohort"]["scope"] == "sector"
    assert explicit["available"] is False
    assert explicit["reason"] == "insufficient_industry_coverage"


@pytest.mark.parametrize(
    ("target", "reason"),
    [
        (None, "target_not_in_snapshot"),
        (_peer_row(1), "target_metric_unavailable"),
    ],
)
def test_peer_multiple_distribution_returns_stable_unavailable_reasons(target, reason):
    rows = [_peer_row(index) for index in range(25)]
    if target is not None:
        target.ps_ratio = 0
        rows[1] = target
    result = build_peer_multiple_distribution(
        target,
        rows,
        ticker="P01.US",
        metric_key="ps_ratio",
    )
    assert result["available"] is False
    assert result["reason"] == reason


@pytest.mark.asyncio
async def test_peer_multiple_service_uses_latest_published_snapshot(db_session):
    snapshot_date = date(2026, 1, 2)
    run = PipelineRun(
        pipeline_name="daily_screener",
        target_date=snapshot_date,
        status="published",
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(DataPublication(
        dataset="screener",
        as_of_date=snapshot_date,
        pipeline_run_id=run.id,
        status="published",
    ))
    rows = [_peer_row(index) for index in range(12)]
    for row in rows:
        row.date = snapshot_date
    db_session.add_all(rows)
    await db_session.commit()

    result = await get_peer_multiple_distribution(
        rows[5].ticker,
        db_session,
        metric_key="ps_ratio",
    )
    outside = await get_peer_multiple_distribution(
        "OUTSIDE.US",
        db_session,
        metric_key="ps_ratio",
    )

    assert result["available"] is True
    assert result["as_of_date"] == snapshot_date.isoformat()
    assert outside["available"] is False
    assert outside["reason"] == "target_not_in_snapshot"
    assert outside["as_of_date"] == snapshot_date.isoformat()


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


@pytest.mark.parametrize(
    ("previous_margin", "current_margin", "severity"),
    [(0.43, 0.40, "warning"), (0.48, 0.40, "high")],
)
def test_margin_warning_exact_decimal_points_are_tolerance_safe(
    previous_margin,
    current_margin,
    severity,
):
    context = _warning_context()
    context["previous_ttm"]["gross_margin"] = previous_margin
    context["current_ttm"]["gross_margin"] = current_margin

    assert _warning(context, "gross_margin_compression")["severity"] == severity


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


def test_cash_decline_uses_supported_cash_equivalent_fallbacks():
    context = _warning_context()
    context["latest_balance"].update({
        "cash": 50.0,
        "cash_and_short_term_investments": None,
    })
    context["prior_year_balance"].update({
        "cash": 100.0,
        "cash_and_short_term_investments": None,
    })

    warning = _warning(context, "cash_decline")
    assert warning["severity"] == "high"
    assert warning["current"] == pytest.approx(50.0)
    assert warning["previous"] == pytest.approx(100.0)


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
    context["prior_year_balance"]["cash"] = None
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
    assert context["prior_year_balance"]["cash_and_short_term_investments"] is None
    assert any(note["code"] == "non_contiguous_previous_ttm" for note in context["data_quality_notes"])
    assert any(note["code"] == "non_comparable_prior_year_balance" for note in context["data_quality_notes"])


def test_balance_warnings_require_the_same_fiscal_quarter_one_year_earlier():
    current = [
        _quarterly_statement("AAA.US", value)
        for value in (
            date(2025, 12, 31),
            date(2025, 9, 30),
            date(2025, 6, 30),
            date(2025, 3, 31),
        )
    ]
    stale = _quarterly_statement("AAA.US", date(2023, 12, 31), scale=4)
    context = build_financial_context([*current, stale])
    result = evaluate_fundamental_warnings(context)

    assert context["prior_year_balance"]["cash_and_short_term_investments"] is None
    assert not {"cash_decline", "debt_increase", "share_dilution"} & {
        warning["id"] for warning in result["warnings"]
    }
    assert any(
        note["code"] == "non_comparable_prior_year_balance"
        for note in result["data_quality_notes"]
    )


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
            "totalStockholderEquity": 5,
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
    assert point["stockholder_equity"] == pytest.approx(5)
    assert point["debt_to_equity"] == pytest.approx(2)
    assert point["shares_outstanding"] == pytest.approx(5)


@pytest.mark.asyncio
async def test_financial_history_chart_uses_cash_fallback_and_split_adjusted_shares(
    db_session,
):
    db_session.add(Ticker(ticker="CHART.US", name="Chart Evidence"))
    db_session.add(DailyPrice(
        ticker="CHART.US",
        date=date(2025, 6, 30),
        open=50,
        high=51,
        low=49,
        close=50,
        adjusted_close=50,
        volume=1_000,
    ))
    db_session.add(FinancialStatement(
        ticker="CHART.US",
        fiscal_date=date(2024, 12, 31),
        period="Yearly",
        revenue=100,
        net_income=20,
        income_statement={
            "totalRevenue": 100,
            "grossProfit": 50,
            "operatingIncome": 25,
            "netIncome": 20,
        },
        cash_flow={"freeCashFlow": 18},
        balance_sheet={
            "cashAndCashEquivalents": 40,
            "totalDebt": 10,
            "totalStockholderEquity": 50,
            "commonStockSharesOutstanding": 100,
        },
    ))
    db_session.add(CorporateAction(
        ticker="CHART.US",
        ex_date=date(2025, 5, 1),
        action_type="split",
        split_factor=2,
        source="EODHD",
        source_id="chart-split-fixture",
    ))
    await db_session.commit()

    result = await get_analyzed_stock_data("CHART.US", db_session)
    point = result["historical_financials"][0]
    assert point["cash_and_short_term_investments"] == pytest.approx(40)
    assert point["shares_reported"] == pytest.approx(100)
    assert point["share_adjustment_factor"] == pytest.approx(2)
    assert point["shares_outstanding"] == pytest.approx(200)


@pytest.mark.asyncio
async def test_analyzer_uses_raw_close_when_adjusted_close_is_missing(db_session):
    db_session.add(Ticker(ticker="FALLBACK.US", name="Fallback Price"))
    db_session.add(DailyPrice(
        ticker="FALLBACK.US",
        date=date(2026, 1, 2),
        open=99,
        high=101,
        low=98,
        close=100,
        adjusted_close=None,
        volume=1_000,
    ))
    await db_session.commit()

    result = await get_analyzed_stock_data("FALLBACK.US", db_session)

    assert result["historical_data"][0]["close"] == pytest.approx(100)


@pytest.mark.asyncio
async def test_decision_support_uses_latest_usable_positive_price(db_session):
    ticker = "PRICE.US"
    db_session.add(Ticker(ticker=ticker, name="Price Fixture"))
    db_session.add_all([
        DailyPrice(
            ticker=ticker,
            date=date(2025, 12, 29),
            close=95,
            adjusted_close=100,
        ),
        DailyPrice(
            ticker=ticker,
            date=date(2025, 12, 30),
            close=110,
            adjusted_close=0,
        ),
        DailyPrice(
            ticker=ticker,
            date=date(2025, 12, 31),
            close=0,
            adjusted_close=None,
        ),
    ])
    await db_session.commit()

    result = await get_decision_support(ticker, db_session)

    assert result["metadata"]["price_date"] == "2025-12-30"
    assert result["valuation"]["current_price"] == pytest.approx(110)
    price_evidence = next(
        item for item in result["evidence"] if item["id"] == "E1"
    )
    assert price_evidence["source_date"] == "2025-12-30"
    assert price_evidence["value"] == pytest.approx(110)


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
    earnings_period_evidence = [
        item
        for item in complete_result["evidence"]
        if item["kind"] == "earnings_quality_period"
    ]
    assert len(complete_result["evidence"]) == 45
    assert len(earnings_period_evidence) == 8
    assert [item["id"] for item in earnings_period_evidence] == [
        f"E{index}" for index in range(38, 46)
    ]
    assert len({item["source_date"] for item in earnings_period_evidence}) == 8
    assert sparse_result["valuation"]["available"] is False
    assert sparse_result["risks"]["warnings"] == []
    assert outside_result["peer_comparison"]["ticker_in_screener"] is False
    assert "outside the latest published Screener universe" in " ".join(outside_result["summary"]["coverage"]["missing_data_reasons"])
    assert negative_result["valuation"]["available"] is False
    assert any(item["severity"] == "high" and item["id"] == "fcf_decline" for item in negative_result["risks"]["warnings"])
    complete_evidence = {item["id"]: item for item in complete_result["evidence"]}
    sparse_evidence = {item["id"]: item for item in sparse_result["evidence"]}
    negative_evidence = {item["id"]: item for item in negative_result["evidence"]}
    assert complete_evidence["E37"]["kind"] == "earnings_quality"
    assert "periods" not in complete_evidence["E37"]["value"]
    assert complete_evidence["E27"]["available"] is True
    assert complete_evidence["E27"]["value"]["assessment"] == "not triggered on available data"
    assert sparse_evidence["E27"]["available"] is False
    assert sparse_evidence["E27"]["value"]["assessment"] == "unavailable"
    assert sparse_evidence["E33"]["available"] is False
    assert sparse_evidence["E32"]["available"] is True
    assert negative_evidence["E30"]["available"] is True
    assert negative_evidence["E30"]["value"]["severity"] == "high"


@pytest.mark.asyncio
async def test_decision_support_loads_only_target_peer_cohorts(db_session):
    from sqlalchemy import event

    from database import engine

    db_session.add(
        Ticker(
            ticker="TARGET.US",
            name="Target",
            sector="Technology",
            industry="Software",
        )
    )
    run = PipelineRun(
        pipeline_name="screener",
        target_date=date(2025, 12, 31),
        status="published",
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(DataPublication(
        dataset="screener",
        as_of_date=date(2025, 12, 31),
        pipeline_run_id=run.id,
        status="published",
    ))
    target = _peer_row(1)
    target.ticker = "TARGET.US"
    same_industry = _peer_row(2, industry="Software", sector="Industrials")
    same_sector = _peer_row(3, industry="Hardware", sector="Technology")
    unrelated = _peer_row(4, industry="Biotechnology", sector="Healthcare")
    for row in (target, same_industry, same_sector, unrelated):
        row.date = date(2025, 12, 31)
    db_session.add_all([target, same_industry, same_sector, unrelated])
    await db_session.commit()

    statements: list[str] = []

    def capture_statement(_conn, _cursor, statement, _parameters, _context, _many):
        if "stock_screener_snapshot" in statement.lower():
            statements.append(statement.lower())

    event.listen(engine.sync_engine, "before_cursor_execute", capture_statement)
    try:
        result = await get_decision_support("TARGET.US", db_session)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture_statement)

    select_statements = [
        statement
        for statement in statements
        if statement.lstrip().startswith("select")
    ]
    assert len(select_statements) == 2
    assert "ticker" in select_statements[0]
    assert "industry" in select_statements[1]
    assert "sector" in select_statements[1]
    assert result["peer_comparison"]["industry_member_count"] == 2
    assert result["peer_comparison"]["sector_member_count"] == 2


@pytest.mark.asyncio
async def test_decision_support_normalizes_statement_shares_for_later_splits(
    db_session,
):
    db_session.add(
        Ticker(
            ticker="SPLIT.US",
            name="Split Fixture",
            currency="USD",
        )
    )
    db_session.add(DailyPrice(
        ticker="SPLIT.US",
        date=date(2025, 6, 30),
        close=50,
        adjusted_close=50,
    ))
    statement_dates = [
        date(2025, 3, 31),
        date(2024, 12, 31),
        date(2024, 9, 30),
        date(2024, 6, 30),
        date(2024, 3, 31),
        date(2023, 12, 31),
        date(2023, 9, 30),
        date(2023, 6, 30),
    ]
    db_session.add_all([
        _quarterly_statement("SPLIT.US", statement_date)
        for statement_date in statement_dates
    ])
    db_session.add(CorporateAction(
        ticker="SPLIT.US",
        ex_date=date(2025, 5, 1),
        action_type="split",
        split_factor=2,
        source="EODHD",
        source_id="split-fixture",
    ))
    await db_session.commit()

    result = await get_decision_support("SPLIT.US", db_session)
    financial_evidence = next(
        item for item in result["evidence"] if item["id"] == "E3"
    )["value"]
    latest = financial_evidence["latest_balance"]
    prior = financial_evidence["prior_year_balance"]
    assert result["metadata"]["currency"] == "USD"
    assert result["valuation"]["inputs"]["shares"] == pytest.approx(200)
    assert latest["shares_reported"] == pytest.approx(100)
    assert latest["share_adjustment_factor"] == pytest.approx(2)
    assert prior["shares"] == pytest.approx(200)
    assert prior["share_adjustment_factor"] == pytest.approx(2)
    assert not any(
        warning["id"] == "share_dilution"
        for warning in result["risks"]["warnings"]
    )
    expected = calculate_dcf_value(
        fcf=100,
        cash=200,
        debt=50,
        shares=200,
        fcf_growth_rate=0.10,
        wacc=0.09,
        perpetual_growth=0.025,
    )["intrinsic_value_per_share"]
    assert result["valuation"]["scenarios"][1][
        "intrinsic_value_per_share"
    ] == pytest.approx(expected)


@pytest.mark.asyncio
async def test_cold_ticker_sync_fetches_and_persists_splits(
    db_session,
    monkeypatch,
):
    ticker = "COLDSPLIT.US"
    requested_splits: list[str] = []

    class FakeClientContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, _exc_type, _exc, _traceback):
            return False

    async def fake_fundamentals(requested_ticker, *, client=None):
        assert client is not None
        return {
            "General": {
                "Name": "Cold Split Fixture",
                "Exchange": "NASDAQ",
                "CurrencyCode": "USD",
            },
            "Financials": {},
        }

    async def fake_prices(requested_ticker, *, client=None):
        assert client is not None
        return [{
            "date": "2025-06-30",
            "open": 50,
            "high": 51,
            "low": 49,
            "close": 50,
            "adjusted_close": 50,
            "volume": 1_000,
        }]

    async def fake_splits(requested_ticker, *, client=None):
        assert client is not None
        requested_splits.append(requested_ticker)
        return [{"date": "2025-05-01", "split": "2/1"}]

    monkeypatch.setattr(
        "services.data_sync.eodhd_client.create_http_client",
        lambda: FakeClientContext(),
    )
    monkeypatch.setattr(
        "services.data_sync.eodhd_client.get_fundamental_data",
        fake_fundamentals,
    )
    monkeypatch.setattr(
        "services.data_sync.eodhd_client.get_eod_historical_data",
        fake_prices,
    )
    monkeypatch.setattr(
        "services.data_sync.eodhd_client.get_splits",
        fake_splits,
    )

    assert await sync_ticker_data(ticker, db_session) is True
    action = (
        await db_session.execute(
            select(CorporateAction).where(CorporateAction.ticker == ticker)
        )
    ).scalar_one()
    assert requested_splits == [ticker]
    assert action.action_type == "split"
    assert float(action.split_factor) == pytest.approx(2)


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


def test_intentionally_empty_server_watchlist_blocks_later_browser_imports():
    from main import app

    headers = {"X-API-Key": "test-secret"}
    with TestClient(app) as client:
        replaced = client.put(
            "/api/personal/watchlist",
            headers=headers,
            json={"tickers": []},
        )
        assert replaced.json() == {"tickers": []}

        attempted_import = client.post(
            "/api/personal/watchlist/import",
            headers=headers,
            json={"tickers": ["AAPL", "NVDA"]},
        )
        assert attempted_import.json() == {"tickers": [], "imported": False}


@pytest.mark.asyncio
async def test_concurrent_first_watchlist_imports_choose_one_authoritative_list():
    from database import async_session_maker
    from services.personal_workspace import get_watchlist, import_watchlist_if_empty

    async def run_import(tickers):
        async with async_session_maker() as session:
            return await import_watchlist_if_empty(session, tickers)

    results = await asyncio.gather(
        run_import(["AAPL"]),
        run_import(["NVDA"]),
    )
    async with async_session_maker() as session:
        authoritative = await get_watchlist(session)

    assert sum(imported for _, imported in results) == 1
    assert authoritative in (["AAPL.US"], ["NVDA.US"])
    assert all(tickers == authoritative for tickers, _ in results)


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
            {"id": "E1", "kind": "price", "available": True, "source_date": "2025-01-01", "value": 10},
            {"id": "E2", "kind": "screener", "available": True, "source_date": "2025-01-01", "value": {"ticker_in_screener": True}},
            {"id": "E5", "kind": "valuation", "available": True, "source_date": "2025-01-01", "value": 12},
            {"id": "E7", "kind": "peer_metric", "available": True, "source_date": "2025-01-01", "value": 80},
            {"id": "E27", "kind": "fundamental_warning", "available": True, "source_date": "2025-01-01", "value": {"triggered": False}},
        ],
    }


def test_ai_citation_validation_rejects_missing_and_unknown_ids():
    allowed = {"E1", "E5", "E7", "E27"}
    validate_evidence_citations(_valid_brief(), allowed)
    with pytest.raises(EvidenceCitationError, match="Unknown"):
        validate_evidence_citations(_valid_brief().replace("[E27]", "[E999]"), allowed)
    with pytest.raises(EvidenceCitationError, match="Risks section"):
        validate_evidence_citations(_valid_brief().replace(" [E27]", ""), allowed)
    with pytest.raises(EvidenceCitationError, match="Every analytical sentence"):
        validate_evidence_citations(
            _valid_brief().replace(
                "The snapshot is available [E1].",
                "The snapshot is available [E1]. Revenue is collapsing.",
            ),
            allowed,
        )


def test_ai_citation_segmentation_handles_legal_company_suffixes():
    allowed = {"E1", "E5", "E7", "E27"}
    validate_evidence_citations(
        _valid_brief().replace(
            "The snapshot is available [E1].",
            "Apple Inc. is covered by the current snapshot [E1].",
        ),
        allowed,
    )
    with pytest.raises(EvidenceCitationError, match="Every analytical sentence"):
        validate_evidence_citations(
            _valid_brief().replace(
                "The snapshot is available [E1].",
                "The issuer is Apple Inc. Revenue is covered [E1].",
            ),
            allowed,
        )


def test_ai_validation_safe_repair_template_is_accepted():
    decision = _decision_for_ai()
    template = _validation_safe_repair_template(decision)
    evidence = decision["evidence"]
    validate_evidence_citations(
        template,
        {item["id"] for item in evidence},
    )
    validate_evidence_numbers(template, evidence)
    validate_evidence_qualitative_claims(template, evidence)
    assert "The Base DCF result is available [E5]." in template
    assert "The cited fundamental warning is not triggered [E27]." in template


def test_ai_validation_safe_repair_template_preserves_decision_signals():
    decision = {
        "evidence": [
            {
                "id": "E2",
                "kind": "screener",
                "available": True,
                "value": {"ticker_in_screener": True},
            },
            {
                "id": "E5",
                "kind": "valuation",
                "available": True,
                "value": {"available": True, "upside_downside": -0.2},
            },
            {
                "id": "E9",
                "kind": "peer_metric",
                "available": True,
                "value": {"summary_scope": "sector"},
            },
            {
                "id": "E30",
                "kind": "fundamental_warning",
                "available": True,
                "value": {"metric": "fcf", "triggered": True},
            },
        ],
    }
    template = _validation_safe_repair_template(decision)
    evidence = decision["evidence"]

    assert "included in the published Screener universe [E2]" in template
    assert "indicates downside to the current price [E5]" in template
    assert "benchmarked at the sector level [E9]" in template
    assert "A cited fundamental warning is triggered [E30]" in template
    validate_evidence_citations(template, {item["id"] for item in evidence})
    validate_evidence_numbers(template, evidence)
    validate_evidence_qualitative_claims(template, evidence)


def test_ai_numeric_validation_accepts_only_numbers_supported_by_cited_evidence():
    evidence = [
        {
            "id": "E1",
            "kind": "price",
            "label": "Current price",
            "source_date": "2026-06-30",
            "value": 10,
        },
        {
            "id": "E3",
            "label": "Quarterly financial coverage",
            "source_date": "2026-06-30",
            "value": {
                "statement_count": 8,
                "revenue": 466_823_000_000,
                "fcf": 5_000_000_000,
                "gross_margin": 0.486529,
            },
        },
        {
            "id": "E5",
            "label": "Base DCF",
            "source_date": "2026-06-30",
            "value": {"intrinsic_value_per_share": 209.067, "upside_downside": -0.3242386},
        },
        {
            "id": "E6",
            "label": "Positive comparison fixture",
            "source_date": "2026-06-30",
            "value": {"upside_downside": 0.3242386},
        },
        {
            "id": "E13",
            "label": "Valuation multiple fixture",
            "source_date": "2026-06-30",
            "value": {"format": "multiple", "metric_value": 32.4},
        },
        {
            "id": "E30",
            "kind": "fundamental_warning",
            "label": "Negative cash-flow fixture",
            "source_date": "2026-06-30",
            "value": {"evidence_id": "E30", "metric": "fcf", "current": -40_000_000},
        },
        {
            "id": "E31",
            "kind": "fundamental_warning",
            "label": "Positive cash-flow fixture",
            "source_date": "2026-06-30",
            "value": {"metric": "fcf", "current": 40_000_000},
        },
        {
            "id": "E32",
            "label": "Rate fixture",
            "source_date": "2026-06-30",
            "value": {"wacc": 0.03},
        },
        {
            "id": "E33",
            "kind": "valuation",
            "label": "DCF cash-flow fixture",
            "source_date": "2026-06-30",
            "value": {
                "enterprise_value": 12_300_000_000,
                "equity_value": 11_900_000_000,
                "projected_fcf": [800_000_000, 900_000_000],
                "present_value_explicit_fcf": 3_500_000_000,
                "present_value_terminal": 8_800_000_000,
            },
        },
    ]
    valid = (
        "As of 2026-06-30, 8 statements show revenue of $466.8 billion "
        "and gross margin of 48.7% [E3].\n"
        "The base case is $209.07 per share, or 32.4% below the current price [E5]."
    )
    validate_evidence_numbers(valid, evidence)
    validate_evidence_numbers("The base case is −32.4% vs current price [E5].", evidence)
    validate_evidence_numbers("The published multiple is 32.4x [E13].", evidence)
    validate_evidence_numbers("The published multiple is 32.4× [E13].", evidence)
    validate_evidence_numbers(
        "3M is covered by the current price record [E1].",
        evidence,
        identity_strings=("3M",),
    )
    validate_evidence_numbers(
        "10x Genomics is covered by the current price record [E1].",
        evidence,
        identity_strings=("10x Genomics", "10x"),
    )
    validate_evidence_numbers(
        "At 3M, the current price is $10 [E1].",
        evidence,
        identity_strings=("3M",),
    )
    validate_evidence_numbers(
        "3M shares trade at $10 [E1].",
        evidence,
        identity_strings=("3M",),
    )
    validate_evidence_numbers("Free cash flow was -$40 million [E30].", evidence)
    validate_evidence_numbers("Free cash flow was −$40M [E30].", evidence)
    validate_evidence_numbers("Free cash flow was $-40MM [E30].", evidence)
    validate_evidence_numbers("Free cash flow was ($40M) [E30].", evidence)
    validate_evidence_numbers("Free cash flow was (40 million) [E30].", evidence)
    validate_evidence_numbers("Downside was (32.4%) [E5].", evidence)
    validate_evidence_numbers("Negative free cash flow of $40 million was recorded [E30].", evidence)
    validate_evidence_numbers("Free cash flow was not yet positive at -$40 million [E30].", evidence)
    validate_evidence_numbers("Free cash flow was not yet negative at +$40 million [E31].", evidence)
    validate_evidence_numbers("Free cash flow was not only negative at -$40 million [E30].", evidence)
    validate_evidence_numbers("Free cash flow was not only deeply negative at -$40 million [E30].", evidence)
    validate_evidence_numbers("Free cash flow was not merely positive at +$40 million [E31].", evidence)
    validate_evidence_numbers("Free cash flow was not only no longer negative at +$40 million [E31].", evidence)
    validate_evidence_numbers("Enterprise value is $12.3 billion [E33].", evidence)
    validate_evidence_numbers("Projected FCF reaches $900 million [E33].", evidence)
    validate_evidence_numbers("The published spread is 300bps [E32].", evidence)
    validate_evidence_numbers(
        "The current price is $10 [E1], while revenue is $466.8 billion [E3].",
        evidence,
    )
    validate_evidence_numbers(
        "The current price is $10 while revenue is $466.8 billion [E1], [E3].",
        evidence,
    )
    validate_evidence_numbers(
        "As of 2026-06-30, the current price is $10 while revenue is "
        "$466.8 billion [E1], [E3].",
        evidence,
    )
    validate_evidence_numbers("According to [E1], the current price is $10.", evidence)

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
        validate_evidence_numbers("The base case has +32.4% downside [E6].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("Positive free cash flow of -$40 million [E30].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("Positive free cash flow of ($40 million) [E30].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("Free cash flow was not yet positive at +$40 million [E31].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("Free cash flow was not yet negative at -$40 million [E30].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("Free cash flow was $466.8 billion [E3].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("Revenue was $5 billion [E3].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("Revenue rather than FCF was $5 billion [E3].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("The base case differs by 32.4% [E5].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("The base case is −32.4% vs current price [E6].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim '40x'"):
        validate_evidence_numbers("The published multiple is 40x [E13].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim '40x'"):
        validate_evidence_numbers("40x is the published multiple [E13].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim '40M'"):
        validate_evidence_numbers("40M was revenue [E13].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("Free cash flow was -$40M [E31].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("Free cash flow was (40 million) [E31].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("WACC was (3%) [E32].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("Free cash flow was $41M [E30].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("The current price is $30 [E1].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("The current price increased 1000% [E1].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("The current-price multiple is 10x [E1].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("The statement count is $8 [E3].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("The published spread is 301bps [E32].", evidence)
    with pytest.raises(EvidenceCitationError, match="citations E1"):
        validate_evidence_numbers(
            "The current price is $466.8 billion [E1], while revenue is $466.8 billion [E3].",
            evidence,
        )
    with pytest.raises(EvidenceCitationError, match="citations E1"):
        validate_evidence_numbers(
            "The current price is $466.8 billion while revenue is $466.8 billion [E1], [E3].",
            evidence,
        )
    with pytest.raises(EvidenceCitationError, match="Adjacent citations are ambiguous"):
        validate_evidence_numbers(
            "As of 2026-06-30, the current price is $466.8 billion [E1], [E3].",
            evidence,
        )
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim '3M'"):
        validate_evidence_numbers(
            "At 3M, share count was 3M [E1].",
            evidence,
            identity_strings=("3M",),
        )
    validate_evidence_numbers(
        "3M stock is trading at $10 [E1].",
        evidence,
        identity_strings=("3M",),
    )
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim '3M'"):
        validate_evidence_numbers(
            "3M shares were outstanding [E1].",
            evidence,
            identity_strings=("3M",),
        )
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("Free cash flow was $30 [E30].", evidence)


def test_company_identity_aliases_strip_legal_suffixes():
    aliases = _company_identity_strings("10x Genomics, Inc.", "TXG.US")
    assert "10x Genomics" in aliases
    assert "10x" in aliases
    assert "TXG.US" in aliases
    validate_evidence_numbers(
        "10x Genomics is covered by the current price record [E1].",
        [{"id": "E1", "kind": "price", "source_date": "2026-06-30", "value": 10}],
        identity_strings=aliases,
    )


def test_sentence_level_date_must_match_each_numeric_claim_citation():
    evidence = [
        {"id": "E1", "kind": "price", "source_date": "2026-06-29", "value": 10},
        {"id": "E3", "source_date": "2026-06-30", "value": {"revenue": 466_800_000_000}},
    ]
    with pytest.raises(EvidenceCitationError, match="Unsupported date claim"):
        validate_evidence_numbers(
            "As of 2026-06-30, the current price is $10 while revenue is "
            "$466.8 billion [E1], [E3].",
            evidence,
        )
    with pytest.raises(EvidenceCitationError, match="Unsupported date claim"):
        validate_evidence_numbers(
            "The current price is $10 [E1] while revenue is $466.8 billion "
            "[E3] as of 2026-06-30.",
            evidence,
        )


def test_semantic_evidence_preserves_period_scope():
    evidence = [{
        "id": "E3",
        "source_date": "2026-06-30",
        "value": {
            "current_ttm": {"revenue": 100_000_000_000},
            "previous_ttm": {"revenue": 80_000_000_000},
        },
    }]
    validate_evidence_numbers("Current revenue was $100 billion [E3].", evidence)
    validate_evidence_numbers("Prior-year revenue was $80 billion [E3].", evidence)
    validate_evidence_numbers("Revenue was previously $80 billion [E3].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("Current revenue was $80 billion [E3].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("Prior-year revenue was $100 billion [E3].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("Revenue was previously $100 billion [E3].", evidence)


def test_earnings_quality_evidence_preserves_period_and_income_basis():
    evidence = [
        {
            "id": "E38",
            "kind": "earnings_quality_period",
            "source_date": "2025-12-31",
            "value": {
                "period_end": "2025-12-31",
                "reported_net_income": 100,
                "analysis": {
                    "result": {
                        "reported_net_income": 100,
                        "normalized_net_income": 120,
                    }
                },
            },
        },
        {
            "id": "E39",
            "kind": "earnings_quality_period",
            "source_date": "2025-09-30",
            "value": {
                "period_end": "2025-09-30",
                "reported_net_income": 120,
                "analysis": {
                    "result": {
                        "reported_net_income": 120,
                        "normalized_net_income": 90,
                    }
                },
            },
        },
    ]

    validate_evidence_numbers(
        "For 2025-12-31, reported net income was $100 [E38].",
        evidence,
    )
    validate_evidence_numbers(
        "For 2025-12-31, normalized net income was $120 [E38].",
        evidence,
    )
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers(
            "For 2025-12-31, reported net income was $120 [E38].",
            evidence,
        )
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers(
            "For 2025-12-31, normalized net income was $100 [E38].",
            evidence,
        )
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers(
            "For 2025-12-31, normalized net income was $90 [E38].",
            evidence,
        )


def test_flag_only_filing_amounts_are_withheld_from_brief_evidence():
    evidence = _earnings_analysis_evidence({
        "id": 7,
        "status": "completed",
        "result": {
            "verification_status": "flag_only",
            "reported_net_income": 100,
            "normalized_net_income": None,
            "adjusted_eps": None,
            "company_adjusted": {
                "label": "Adjusted earnings",
                "adjusted_net_income": 999,
                "adjusted_diluted_eps": 9.99,
            },
            "adjustments": [{
                "category": "impairment",
                "label": "Unverified item",
                "pretax_earnings_effect": 500,
                "tax_effect": 100,
                "earnings_effect_after_tax": 400,
                "include_in_normalized": False,
                "recurring": False,
                "cash_effect": "non_cash",
                "citation": {},
            }],
        },
    })

    assert evidence["result"]["reported_net_income"] == 100
    assert evidence["result"]["company_adjusted"] is None
    assert evidence["result"]["adjustments"][0]["pretax_earnings_effect"] is None
    assert evidence["result"]["adjustments"][0]["tax_effect"] is None
    assert evidence["result"]["adjustments"][0]["earnings_effect_after_tax"] is None


def test_ai_qualitative_directions_must_agree_with_cited_periods():
    evidence = [
        {
            "id": "E3",
            "source_date": "2026-06-30",
            "value": {
                "current_ttm": {
                    "revenue": 80_000_000_000,
                    "fcf": -10_000_000,
                },
                "previous_ttm": {
                    "revenue": 100_000_000_000,
                    "fcf": 20_000_000,
                },
            },
        },
        {
            "id": "E31",
            "kind": "fundamental_warning",
            "source_date": "2026-06-30",
            "value": {
                "title": "Weak cash conversion",
                "metric": "fcf_net_income_conversion",
                "current": 0.20,
                "triggered": True,
            },
        },
        {
            "id": "E27",
            "kind": "fundamental_warning",
            "source_date": "2026-06-30",
            "value": {
                "metric": "revenue",
                "triggered": False,
                "assessment": "not triggered on available data",
            },
        },
        {
            "id": "E30",
            "kind": "fundamental_warning",
            "source_date": "2026-06-30",
            "value": {
                "metric": "fcf",
                "triggered": True,
                "title": "TTM free-cash-flow decline",
            },
        },
    ]
    validate_evidence_qualitative_claims(
        "Revenue is declining and free cash flow is negative [E3].",
        evidence,
    )
    validate_evidence_qualitative_claims(
        "FCF to net income conversion is weak [E31].",
        evidence,
    )
    validate_evidence_qualitative_claims(
        "Free cash flow was previously positive [E3].",
        evidence,
    )
    validate_evidence_qualitative_claims(
        "The revenue decline warning was not triggered [E27].",
        evidence,
    )
    validate_evidence_qualitative_claims(
        "E27 was not triggered [E27].",
        evidence,
    )
    validate_evidence_qualitative_claims(
        "The FCF decline warning was triggered [E30].",
        evidence,
    )
    with pytest.raises(EvidenceCitationError, match="qualitative direction"):
        validate_evidence_qualitative_claims(
            "Revenue is growing [E3].",
            evidence,
        )
    with pytest.raises(EvidenceCitationError, match="Negated qualitative"):
        validate_evidence_qualitative_claims(
            "Revenue did not decline [E3].",
            evidence,
        )
    with pytest.raises(EvidenceCitationError, match="qualitative sign"):
        validate_evidence_qualitative_claims(
            "Free cash flow is positive [E3].",
            evidence,
        )
    with pytest.raises(EvidenceCitationError, match="qualitative sign"):
        validate_evidence_qualitative_claims(
            "Free cash flow was previously negative [E3].",
            evidence,
        )
    with pytest.raises(EvidenceCitationError, match="Negated qualitative"):
        validate_evidence_qualitative_claims(
            "Free cash flow is not negative [E3].",
            evidence,
        )
    with pytest.raises(EvidenceCitationError, match="evaluative claim"):
        validate_evidence_qualitative_claims(
            "Revenue is strong [E3].",
            evidence,
        )
    with pytest.raises(EvidenceCitationError, match="evaluative claim"):
        validate_evidence_qualitative_claims(
            "Revenue is weak [E31].",
            evidence,
        )
    with pytest.raises(EvidenceCitationError, match="trigger polarity"):
        validate_evidence_qualitative_claims(
            "The revenue decline warning was triggered [E27].",
            evidence,
        )
    with pytest.raises(EvidenceCitationError, match="trigger polarity"):
        validate_evidence_qualitative_claims(
            "E27 was triggered [E27].",
            evidence,
        )
    with pytest.raises(EvidenceCitationError, match="not supported"):
        validate_evidence_qualitative_claims(
            "The revenue decline warning was triggered [E30].",
            evidence,
        )


def test_semantic_evidence_tags_warning_percentages_and_peer_metrics():
    evidence = [
        {
            "id": "E30",
            "kind": "fundamental_warning",
            "source_date": "2026-06-30",
            "value": {
                "metric": "fcf_change",
                "evidence_metric": "fcf",
                "current": 70_000_000,
                "previous": 100_000_000,
                "message": "TTM free cash flow declined 30.0% year over year.",
            },
        },
        {
            "id": "E13",
            "kind": "peer_metric",
            "source_date": "2026-06-30",
            "value": {
                "metric_key": "gross_margin",
                "metric_value": 0.487,
                "format": "percent",
                "summary_percentile": 90.0,
            },
        },
        {
            "id": "E25",
            "kind": "peer_metric",
            "source_date": "2026-06-30",
            "value": {
                "metric_key": "debt_to_equity",
                "metric_value": 1.25,
                "format": "multiple",
            },
        },
        {
            "id": "E8",
            "kind": "peer_metric",
            "source_date": "2026-06-30",
            "value": {
                "metric_key": "sales_growth_3yr",
                "metric_value": 0.123,
                "format": "percent",
            },
        },
        {
            "id": "E16",
            "kind": "peer_metric",
            "source_date": "2026-06-30",
            "value": {
                "metric_key": "ps_ratio",
                "metric_value": 3.2,
                "format": "multiple",
            },
        },
        {
            "id": "E28",
            "kind": "fundamental_warning",
            "source_date": "2026-06-30",
            "value": {
                "metric": "margin_compression",
                "evidence_metric": "gross_margin",
                "current": 0.40,
                "previous": 0.43,
                "message": "Gross margin compression was 3.0 percentage points year over year.",
            },
        },
    ]
    validate_evidence_numbers("Free cash flow declined 30.0% [E30].", evidence)
    validate_evidence_numbers("Gross margin is 48.7% [E13].", evidence)
    validate_evidence_numbers("Gross margin ranks in the 90th percentile [E13].", evidence)
    validate_evidence_numbers("Debt to equity is 1.25x [E25].", evidence)
    validate_evidence_numbers("Sales growth (3Y) is 12.3% [E8].", evidence)
    validate_evidence_numbers("Price / sales is 3.2x [E16].", evidence)
    validate_evidence_numbers("Gross margin compressed by 3.0 percentage points [E28].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("Free cash flow declined 31.0% [E30].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("Gross margin is 90 points [E13].", evidence)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers("Gross margin was 3.0 percentage points [E28].", evidence)


def test_semantic_peer_facets_preserve_percentile_type_scope_and_coverage_role():
    evidence = [{
        "id": "E7",
        "kind": "peer_metric",
        "source_date": "2026-06-30",
        "value": {
            "metric_key": "pe_ratio",
            "metric_value": 20.0,
            "format": "multiple",
            "industry": {
                "metric_key": "pe_ratio",
                "observation_count": 7,
                "minimum_observations": 10,
                "raw_percentile": 90.0,
                "desirability_percentile": 10.0,
            },
            "sector": {
                "metric_key": "pe_ratio",
                "observation_count": 23,
                "minimum_observations": 20,
                "raw_percentile": 80.0,
                "desirability_percentile": 20.0,
            },
            "summary_scope": "industry",
            "summary_percentile": 10.0,
        },
    }]
    validate_evidence_numbers(
        "P/E has an industry raw percentile of 90 [E7].",
        evidence,
    )
    validate_evidence_numbers(
        "P/E has an industry desirability percentile of 10 [E7].",
        evidence,
    )
    validate_evidence_numbers("P/E ranks in the 10th percentile [E7].", evidence)
    validate_evidence_numbers(
        "Industry P/E coverage has 7 observations [E7].",
        evidence,
    )
    validate_evidence_numbers(
        "Industry P/E coverage requires 10 observations [E7].",
        evidence,
    )
    validate_evidence_numbers(
        "Sector P/E coverage has 23 observations [E7].",
        evidence,
    )
    validate_evidence_numbers(
        "Sector P/E coverage requires 20 observations [E7].",
        evidence,
    )
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers(
            "P/E has an industry desirability percentile of 90 [E7].",
            evidence,
        )
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers(
            "Industry P/E coverage has 23 observations [E7].",
            evidence,
        )
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers(
            "Industry P/E coverage requires 7 observations [E7].",
            evidence,
        )


def test_semantic_peer_facets_bind_only_the_numeric_phrase_they_modify():
    evidence = [{
        "id": "E13",
        "kind": "peer_metric",
        "source_date": "2026-06-30",
        "value": {
            "metric_key": "gross_margin",
            "metric_value": 0.487,
            "format": "percent",
            "industry": {
                "metric_key": "gross_margin",
                "observation_count": 12,
                "minimum_observations": 10,
                "raw_percentile": 90.0,
                "desirability_percentile": 90.0,
            },
            "summary_scope": "industry",
            "summary_percentile": 90.0,
        },
    }]
    validate_evidence_numbers(
        "Gross margin is 48.7%, ranking in the 90th percentile [E13].",
        evidence,
    )
    validate_evidence_numbers(
        "Across 12 valid industry peers, gross margin is 48.7% [E13].",
        evidence,
    )


def test_semantic_warning_transition_levels_remain_period_scoped():
    evidence = [{
        "id": "E30",
        "kind": "fundamental_warning",
        "source_date": "2026-06-30",
        "value": {
            "metric": "fcf_change",
            "evidence_metric": "fcf",
            "current": 70_000_000,
            "previous": 100_000_000,
            "message": "TTM free cash flow declined 30.0% year over year.",
        },
    }]
    validate_evidence_numbers(
        "Free cash flow declined to $70 million [E30].",
        evidence,
    )
    validate_evidence_numbers(
        "Free cash flow declined from $100 million to $70 million [E30].",
        evidence,
    )
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers(
            "Free cash flow declined to $100 million [E30].",
            evidence,
        )


def test_semantic_projected_fcf_preserves_forecast_position():
    evidence = [{
        "id": "E4",
        "kind": "valuation",
        "source_date": "2026-06-30",
        "value": {"projected_fcf": [800_000_000, 900_000_000]},
    }]
    validate_evidence_numbers(
        "Initial projected FCF is $800 million [E4].",
        evidence,
    )
    validate_evidence_numbers(
        "Final projected FCF is $900 million [E4].",
        evidence,
    )
    validate_evidence_numbers(
        "Year 1 projected FCF is $800M and year 2 projected FCF is $900M [E4].",
        evidence,
    )
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers(
            "Initial projected FCF is $900 million [E4].",
            evidence,
        )
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers(
            "Final projected FCF is $800 million [E4].",
            evidence,
        )
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers(
            "Year 1 projected FCF is $900M [E4].",
            evidence,
        )


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
    changed_currency = deepcopy(decision)
    changed_currency["metadata"]["currency"] = "JPY"
    changed_label = deepcopy(decision)
    changed_label["evidence"][0]["label"] = "Corrected current-price label"
    changed_kind = deepcopy(decision)
    changed_kind["evidence"][0]["kind"] = "corrected_price_kind"
    assert len({
        baseline,
        build_evidence_hash(changed_value, "model-a"),
        build_evidence_hash(changed_date, "model-a"),
        build_evidence_hash(changed_assumption, "model-a"),
        build_evidence_hash(changed_identity, "model-a"),
        build_evidence_hash(changed_currency, "model-a"),
        build_evidence_hash(changed_label, "model-a"),
        build_evidence_hash(changed_kind, "model-a"),
        build_evidence_hash(decision, "model-b"),
    }) == 9


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
async def test_concurrent_ai_cache_misses_share_one_provider_generation(monkeypatch):
    from database import async_session_maker

    decision = _decision_for_ai()
    monkeypatch.setattr("services.ai_assistant.settings.DEEPSEEK_API_KEY", "configured")
    monkeypatch.setattr("services.ai_assistant.settings.DEEPSEEK_MODEL", "test-model")
    generation_started = asyncio.Event()
    release_generation = asyncio.Event()
    calls = 0

    async def generate(_prompt):
        nonlocal calls
        calls += 1
        generation_started.set()
        await release_generation.wait()
        return _valid_brief()

    async def request_report():
        async with async_session_maker() as session:
            return "".join([
                chunk
                async for chunk in generate_stock_report(
                    "AAA.US",
                    decision,
                    session,
                )
            ])

    monkeypatch.setattr("services.ai_assistant.generate_deepseek_text", generate)
    first = asyncio.create_task(request_report())
    await generation_started.wait()
    second = asyncio.create_task(request_report())
    await asyncio.sleep(0.05)
    assert calls == 1

    release_generation.set()
    first_result, second_result = await asyncio.gather(first, second)
    async with async_session_maker() as session:
        count = (
            await session.execute(select(func.count(DecisionBriefCache.id)))
        ).scalar_one()
    assert first_result == _valid_brief()
    assert second_result == first_result
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
async def test_ai_unsupported_qualitative_claims_are_not_cached(
    db_session,
    monkeypatch,
):
    decision = _decision_for_ai()
    decision["evidence"].append({
        "id": "E3",
        "source_date": "2026-06-30",
        "value": {
            "current_ttm": {"revenue": 80},
            "previous_ttm": {"revenue": 100},
        },
    })
    monkeypatch.setattr("services.ai_assistant.settings.DEEPSEEK_API_KEY", "configured")
    monkeypatch.setattr("services.ai_assistant.settings.DEEPSEEK_MODEL", "test-model")

    async def generate(_prompt):
        return _valid_brief().replace(
            "The snapshot is available [E1].",
            "Revenue is growing [E3].",
        )

    monkeypatch.setattr("services.ai_assistant.generate_deepseek_text", generate)
    response = "".join([
        chunk
        async for chunk in generate_stock_report(
            "AAA.US",
            decision,
            db_session,
        )
    ])
    count = (
        await db_session.execute(select(func.count(DecisionBriefCache.id)))
    ).scalar_one()
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
        assert "exactly one sentence under each heading" in prompt
        assert "Return exactly this Markdown" in prompt
        assert "The cited fundamental warning is not triggered [E27]." in prompt
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
    expected = {
        "personal_watchlist_items",
        "personal_workspace_state",
        "ticker_valuation_scenarios",
        "decision_brief_cache",
    }
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
