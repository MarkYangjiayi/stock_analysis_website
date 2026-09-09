"""Auditable company-specific assumptions for the decision-cockpit DCF.

Provider/database loading stays in ``decision_support`` while the finance rules
here remain pure, unit-testable, and reproducible outside the web application.
"""

from __future__ import annotations

import math
from statistics import median
from typing import Any


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _growth(current: Any, previous: Any) -> float | None:
    current_value = _number(current)
    previous_value = _number(previous)
    if current_value is None or previous_value is None or previous_value <= 0:
        return None
    result = current_value / previous_value - 1
    return result if -0.75 <= result <= 2.0 else None


def estimate_company_wacc(
    *,
    market_cap: Any,
    beta: Any,
    latest_debt: Any,
    prior_debt: Any,
    average_debt: Any,
    interest_expense: Any,
    income_tax_expense: Any,
    income_before_tax: Any,
    current_price: Any,
    shares: Any,
    risk_free_rate: float,
    equity_risk_premium: float,
    fallback_debt_spread: float,
    fallback_tax_rate: float,
    assumptions_as_of: str,
    beta_source: str | None = None,
) -> dict[str, Any]:
    """Estimate one current WACC using the same broad structure as GuruFocus."""

    notes: list[str] = []
    observed_beta = _number(beta)
    resolved_beta_source = beta_source or "provided_beta"
    if observed_beta is None or not 0 < observed_beta <= 3:
        observed_beta = 1.0
        resolved_beta_source = "fallback_market_beta"
        notes.append("A usable provider/local beta was unavailable; market beta 1.0 was used.")

    equity_value = _number(market_cap)
    equity_source = "screener_market_cap"
    if equity_value is None or equity_value <= 0:
        price_value = _number(current_price)
        share_value = _number(shares)
        if price_value is not None and price_value > 0 and share_value is not None and share_value > 0:
            equity_value = price_value * share_value
            equity_source = "price_times_statement_shares"
            notes.append("Market capitalization was reconstructed from price and shares.")

    latest_debt_value = _number(latest_debt)
    prior_debt_value = _number(prior_debt)
    valid_debts = [
        value
        for value in (latest_debt_value, prior_debt_value)
        if value is not None and value >= 0
    ]
    provided_average_debt = _number(average_debt)
    if provided_average_debt is not None and provided_average_debt >= 0:
        average_book_debt = provided_average_debt
        debt_source = "average_four_quarter_book_debt"
    else:
        average_book_debt = sum(valid_debts) / len(valid_debts) if valid_debts else None
        debt_source = "average_latest_and_prior_year_book_debt"
    if provided_average_debt is None and len(valid_debts) == 1:
        debt_source = "latest_available_book_debt"
        notes.append("Only one debt observation was available; it was used instead of a one-year average.")

    tax_expense = _number(income_tax_expense)
    pretax_income = _number(income_before_tax)
    if pretax_income is not None and pretax_income > 0 and tax_expense is not None and tax_expense >= 0:
        tax_rate = min(max(tax_expense / pretax_income, 0.0), 1.0)
        tax_source = "ttm_income_tax_over_pretax_income"
    elif pretax_income is not None and pretax_income <= 0:
        tax_rate = 0.0
        tax_source = "loss_company_zero_tax_rate"
        notes.append("TTM pretax income was non-positive; the WACC tax shield was set to zero.")
    else:
        tax_rate = fallback_tax_rate
        tax_source = "fallback_tax_rate"
        notes.append("A usable TTM effective tax rate was unavailable; the configured fallback was used.")

    reported_interest = _number(interest_expense)
    if reported_interest is not None:
        reported_interest = abs(reported_interest)
    if average_book_debt == 0:
        cost_of_debt = 0.0
        debt_cost_source = "debt_free"
        interest_for_fcff = 0.0
    elif (
        average_book_debt is not None
        and average_book_debt > 0
        and reported_interest is not None
        and reported_interest > 0
        and 0 < reported_interest / average_book_debt <= 0.25
    ):
        cost_of_debt = reported_interest / average_book_debt
        debt_cost_source = "ttm_interest_over_average_book_debt"
        interest_for_fcff = reported_interest
    elif average_book_debt is not None and average_book_debt > 0:
        cost_of_debt = risk_free_rate + fallback_debt_spread
        debt_cost_source = "risk_free_plus_fallback_spread"
        interest_for_fcff = average_book_debt * cost_of_debt
        notes.append("Reported interest did not produce a usable debt cost; a configured spread was used.")
    else:
        cost_of_debt = None
        debt_cost_source = "unavailable"
        interest_for_fcff = None

    cost_of_equity = risk_free_rate + observed_beta * equity_risk_premium
    unavailable_reasons: list[str] = []
    if equity_value is None or equity_value <= 0:
        unavailable_reasons.append("Positive market capitalization is required to estimate WACC.")
    if average_book_debt is None:
        unavailable_reasons.append("Book debt is required to estimate WACC.")
    if cost_of_debt is None:
        unavailable_reasons.append("Cost of debt could not be estimated.")

    wacc = None
    equity_weight = None
    debt_weight = None
    if not unavailable_reasons:
        total_capital = equity_value + average_book_debt
        if total_capital <= 0:
            unavailable_reasons.append("Positive debt plus equity capital is required to estimate WACC.")
        else:
            equity_weight = equity_value / total_capital
            debt_weight = average_book_debt / total_capital
            wacc = equity_weight * cost_of_equity + debt_weight * cost_of_debt * (1 - tax_rate)
            if not 0.03 <= wacc <= 0.25:
                unavailable_reasons.append("Estimated WACC is outside the supported 3% to 25% range.")
                wacc = None

    fallback_used = any(
        source.startswith("fallback") or "fallback" in source
        for source in (resolved_beta_source, tax_source, debt_cost_source)
    )
    return {
        "available": wacc is not None,
        "wacc": wacc,
        "method": "CAPM plus after-tax book-debt cost",
        "quality": "estimated_with_fallbacks" if fallback_used else "calculated",
        "assumptions_as_of": assumptions_as_of,
        "risk_free_rate": risk_free_rate,
        "equity_risk_premium": equity_risk_premium,
        "beta": observed_beta,
        "beta_source": resolved_beta_source,
        "cost_of_equity": cost_of_equity,
        "equity_value": equity_value,
        "equity_source": equity_source,
        "average_debt": average_book_debt,
        "debt_source": debt_source,
        "cost_of_debt": cost_of_debt,
        "debt_cost_source": debt_cost_source,
        "tax_rate": tax_rate,
        "tax_source": tax_source,
        "equity_weight": equity_weight,
        "debt_weight": debt_weight,
        "interest_expense_for_fcff": interest_for_fcff,
        "notes": notes,
        "unavailable_reasons": unavailable_reasons,
    }


def derive_growth_scenarios(
    *,
    current_fcf: Any,
    previous_fcf: Any,
    current_revenue: Any,
    previous_revenue: Any,
    sales_growth_3yr: Any,
    sales_growth_5yr: Any,
    fallback_growth: float,
) -> dict[str, Any]:
    """Build robust five-year FCF growth cases from company growth evidence."""

    candidates = (
        ("ttm_fcf_yoy", _growth(current_fcf, previous_fcf)),
        ("ttm_revenue_yoy", _growth(current_revenue, previous_revenue)),
        ("sales_growth_3yr", _number(sales_growth_3yr)),
        ("sales_growth_5yr", _number(sales_growth_5yr)),
    )
    signals = [
        {"source": source, "raw_value": value, "winsorized_value": min(max(value, -0.25), 0.50)}
        for source, value in candidates
        if value is not None and -0.75 <= value <= 2.0
    ]
    if signals:
        raw_base = median(item["winsorized_value"] for item in signals)
        base_growth = min(max(raw_base, -0.05), 0.20)
        method = "median_of_company_growth_signals"
    else:
        raw_base = fallback_growth
        base_growth = fallback_growth
        method = "configured_fallback"

    if len(signals) >= 2:
        values = [item["winsorized_value"] for item in signals]
        spread = min(max((max(values) - min(values)) / 2, 0.05), 0.10)
    else:
        spread = 0.05
    return {
        "method": method,
        "signals": signals,
        "raw_base_growth": raw_base,
        "base_growth": base_growth,
        "scenario_spread": spread,
        "bear_growth": max(base_growth - spread, -0.20),
        "bull_growth": min(base_growth + spread, 0.50),
    }


def convert_reported_fcf_to_fcff(
    reported_fcf: Any,
    *,
    interest_expense_for_fcff: Any,
    tax_rate: Any,
) -> dict[str, Any]:
    """Convert CFO-minus-capex style provider FCF to an FCFF estimate."""

    fcf = _number(reported_fcf)
    interest = _number(interest_expense_for_fcff)
    effective_tax = _number(tax_rate)
    if fcf is None:
        return {"available": False, "fcff": None, "reported_fcf": None, "after_tax_interest_adjustment": None, "reason": "Reported free cash flow is unavailable."}
    if interest is None or effective_tax is None:
        return {"available": False, "fcff": None, "reported_fcf": fcf, "after_tax_interest_adjustment": None, "reason": "After-tax interest is required to align free cash flow with WACC."}
    adjustment = abs(interest) * (1 - min(max(effective_tax, 0.0), 1.0))
    return {
        "available": True,
        "fcff": fcf + adjustment,
        "reported_fcf": fcf,
        "after_tax_interest_adjustment": adjustment,
        "reason": None,
    }
