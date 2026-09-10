import pytest

from services.valuation_assumptions import (
    convert_reported_fcf_to_fcff,
    derive_growth_scenarios,
    estimate_company_wacc,
)


def test_company_wacc_matches_capm_and_after_tax_debt_formula():
    result = estimate_company_wacc(
        market_cap=900,
        beta=1.2,
        latest_debt=110,
        prior_debt=90,
        average_debt=100,
        interest_expense=5,
        income_tax_expense=20,
        income_before_tax=100,
        current_price=None,
        shares=None,
        risk_free_rate=0.04,
        equity_risk_premium=0.06,
        fallback_debt_spread=0.015,
        fallback_tax_rate=0.21,
        assumptions_as_of="2026-09-08",
    )

    cost_of_equity = 0.04 + ((2 * 1.2 + 1) / 3) * 0.06
    expected = (900 / 1010) * cost_of_equity + (110 / 1010) * 0.05 * (1 - 0.20)
    assert result["available"] is True
    assert result["quality"] == "calculated"
    assert result["wacc"] == pytest.approx(expected)
    assert result["cost_of_debt"] == pytest.approx(0.05)
    assert result["tax_rate"] == pytest.approx(0.20)


def test_company_wacc_exposes_fallbacks_instead_of_silent_universal_rate():
    result = estimate_company_wacc(
        market_cap=None,
        beta=None,
        latest_debt=100,
        prior_debt=None,
        average_debt=None,
        interest_expense=None,
        income_tax_expense=None,
        income_before_tax=None,
        current_price=20,
        shares=50,
        risk_free_rate=0.04,
        equity_risk_premium=0.06,
        fallback_debt_spread=0.015,
        fallback_tax_rate=0.21,
        assumptions_as_of="2026-09-08",
    )

    assert result["available"] is True
    assert result["quality"] == "estimated_with_fallbacks"
    assert result["beta_source"] == "fallback_market_beta"
    assert result["equity_source"] == "price_times_statement_shares"
    assert result["debt_cost_source"] == "risk_free_plus_fallback_spread"
    assert result["notes"]


def test_growth_cases_use_robust_company_signal_median_and_shared_spread():
    result = derive_growth_scenarios(
        current_fcf=120,
        previous_fcf=100,
        current_revenue=108,
        previous_revenue=100,
        sales_growth_3yr=0.10,
        sales_growth_5yr=0.06,
        fallback_growth=0.05,
    )

    assert result["method"] == "historical_revenue_median_constant_cash_flow_margin"
    assert result["base_growth"] == pytest.approx(0.08)
    assert result["scenario_spread"] == pytest.approx(0.05)
    assert result["bear_growth"] == pytest.approx(0.03)
    assert result["bull_growth"] == pytest.approx(0.13)


def test_reported_fcf_is_unlevered_before_wacc_discounting():
    result = convert_reported_fcf_to_fcff(
        100,
        interest_expense_for_fcff=10,
        tax_rate=0.25,
    )

    assert result["available"] is True
    assert result["after_tax_interest_adjustment"] == pytest.approx(7.5)
    assert result["fcff"] == pytest.approx(107.5)
