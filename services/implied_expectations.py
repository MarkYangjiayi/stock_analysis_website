"""What the share price requires of the business.

The historical-growth DCF starts from trailing free cash flow, which is
negative or distorted while a company is investing heavily. This model starts
from revenue: its growth, the operating margin it settles at, and the capital
each new dollar of revenue needs. Given the price it solves for one of them,
or for the annual return the price offers if consensus is met.

One price fits many combinations, so every answer holds the other inputs
fixed and says which.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any, Callable, Optional, Sequence

from services.dcf import (
    FORECAST_YEARS,
    INITIAL_GROWTH_YEARS,
    calculate_dcf_value,
    operating_forecast_cash_flows,
)

REQUIRED_RETURNS = (0.08, 0.10, 0.12)
DEFAULT_REQUIRED_RETURN = 0.10
# Above this, revenue needs so little capital that reinvestment stops mattering.
MAX_SALES_TO_CAPITAL = 5.0
GROWTH_RANGE = (-0.5, 1.0)
MARGIN_RANGE = (0.005, 0.8)
SALES_TO_CAPITAL_RANGE = (0.05, 50.0)
MIN_RETURN = 0.03
MAX_RETURN = 0.5
HISTORY_YEARS = 5


def growth_path(initial_growth: float, terminal_growth: float) -> list[float]:
    """Five years at one rate, then a linear fade to terminal growth: the DCF kernel's shape."""
    fade_years = FORECAST_YEARS - INITIAL_GROWTH_YEARS
    return [
        initial_growth + (terminal_growth - initial_growth) * max(0, year - INITIAL_GROWTH_YEARS) / fade_years
        for year in range(1, FORECAST_YEARS + 1)
    ]


def consensus_growth_path(next_year: float, following_year: float, terminal_growth: float) -> list[float]:
    """Two consensus years, then a linear fade to terminal growth by year ten."""
    fade_years = FORECAST_YEARS - 2
    return [next_year, following_year] + [
        following_year + (terminal_growth - following_year) * step / fade_years
        for step in range(1, fade_years + 1)
    ]


def operating_forecast_rows(
    *,
    revenue: float,
    start_margin: float,
    target_margin: float,
    growth_rates: Sequence[float],
    tax_rate: float,
    sales_to_capital: float,
) -> list[dict[str, Any]]:
    """Ten forecast rows in the DCF kernel's format; margin reaches its target in year five."""
    rows = []
    previous = revenue
    for year, growth in enumerate(growth_rates, 1):
        current = previous * (1 + growth)
        progress = min(year, INITIAL_GROWTH_YEARS) / INITIAL_GROWTH_YEARS
        rows.append({
            "year": year,
            "revenue": current,
            "operating_margin": start_margin + (target_margin - start_margin) * progress,
            "tax_rate": tax_rate,
            # The kernel nets capex, depreciation and working capital, so
            # reinvestment enters as one figure. Shrinking revenue does not
            # hand invested capital back.
            "capex": max(current - previous, 0.0) / sales_to_capital,
            "depreciation": 0.0,
            "change_in_working_capital": 0.0,
            "source": "implied from price",
        })
        previous = current
    return rows


def enterprise_value(*, discount_rate: float, terminal_growth: float, **path: Any) -> Optional[float]:
    """Value of one path, or None when it leaves no positive terminal cash flow.

    Terminal return on capital equals the discount rate: no excess return on
    new investment after year ten.
    """
    try:
        explicit, terminal = operating_forecast_cash_flows(
            operating_forecast_rows(**path), terminal_growth, discount_rate
        )
        return calculate_dcf_value(
            fcf=0, cash=0, debt=0, shares=1, fcf_growth_rate=0,
            wacc=discount_rate, perpetual_growth=terminal_growth,
            explicit_fcff=explicit, terminal_fcff=terminal,
        )["enterprise_value"]
    except ValueError:
        return None


def _solve(value_at: Callable[[float], Optional[float]], target: float, low: float, high: float) -> Optional[float]:
    """Bisect for value_at(x) == target; None when the range does not bracket it."""
    low_value, high_value = value_at(low), value_at(high)
    if low_value is None or high_value is None or (low_value - target) * (high_value - target) > 0:
        return None
    for _ in range(60):
        middle = (low + high) / 2
        middle_value = value_at(middle)
        if middle_value is None:
            return None
        if (middle_value - target) * (low_value - target) > 0:
            low, low_value = middle, middle_value
        else:
            high = middle
    return (low + high) / 2


def history_references(yearly_points: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Revenue growth and the operating-margin range over the last five fiscal years.

    ``yearly_points`` are annual statement points, newest first.
    """
    points = [point for point in yearly_points if (point.get("revenue") or 0) > 0][: HISTORY_YEARS + 1]
    margins = [
        point["operating_income"] / point["revenue"]
        for point in points[:HISTORY_YEARS]
        if point.get("operating_income") is not None
    ]
    years = len(points) - 1
    return {
        "revenue_growth": (points[0]["revenue"] / points[-1]["revenue"]) ** (1 / years) - 1 if years >= 3 else None,
        "revenue_growth_years": years if years >= 3 else None,
        "operating_margin_low": min(margins) if len(margins) >= 3 else None,
        "operating_margin_high": max(margins) if len(margins) >= 3 else None,
    }


def consensus_references(
    fiscal_years: Sequence[dict[str, Any]],
    *,
    trailing_revenue: float,
    trailing_period_end: date,
    shares: float,
    tax_rate: float,
) -> Optional[dict[str, Any]]:
    """Consensus growth for the next two years and the operating margin it implies.

    ``fiscal_years`` are the current and next fiscal-year consensus rows. The
    next twelve months blend the two by how much of the current fiscal year
    is still ahead of the trailing period.
    """
    if len(fiscal_years) != 2:
        return None
    current, following = fiscal_years
    current_revenue, following_revenue = current.get("revenue"), following.get("revenue")
    if not current_revenue or not following_revenue or following["period_end"] <= current["period_end"]:
        return None
    remaining = min(max((current["period_end"] - trailing_period_end).days / 365, 0.0), 1.0)
    next_twelve_months = remaining * current_revenue + (1 - remaining) * following_revenue
    # Earnings per share over revenue, grossed up for tax. It ignores net
    # interest and non-operating items and inherits the provider's EPS basis.
    margin = None
    if following.get("eps"):
        margin = following["eps"] * shares / (1 - tax_rate) / following_revenue
        if not 0 < margin < 1:
            margin = None
    return {
        "next_year_growth": next_twelve_months / trailing_revenue - 1,
        "following_year_growth": following_revenue / current_revenue - 1,
        "operating_margin": margin,
        "operating_margin_basis": "consensus_eps_over_revenue_before_tax_approximation" if margin is not None else None,
        "fiscal_year_ends": [current["period_end"].isoformat(), following["period_end"].isoformat()],
    }


def _position(value: Optional[float], references: Sequence[Optional[float]]) -> Optional[str]:
    known = [reference for reference in references if reference is not None]
    if value is None or not known:
        return None
    if value > max(known):
        return "above_references"
    if value < min(known):
        return "below_references"
    return "within_references"


def build_implied_expectations(
    *,
    current_price: Optional[float],
    shares: Optional[float],
    cash: Optional[float],
    debt: Optional[float],
    equity_adjustment: float = 0,
    equity: Optional[float],
    revenue: Optional[float],
    operating_margin: Optional[float],
    tax_rate: float,
    terminal_growth: float,
    history: dict[str, Any],
    consensus: Optional[dict[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "available": False,
        "reasons": [],
        "model": {
            "forecast_years": FORECAST_YEARS,
            "initial_growth_years": INITIAL_GROWTH_YEARS,
            "margin_convergence_years": INITIAL_GROWTH_YEARS,
            "terminal_growth": terminal_growth,
            "terminal_return_on_capital": "equal_to_discount_rate",
        },
        "required_returns": list(REQUIRED_RETURNS),
        "default_required_return": DEFAULT_REQUIRED_RETURN,
        "references": {"history": history, "consensus": consensus},
    }
    missing = [
        label
        for label, value in (
            ("a positive current price", current_price if current_price and current_price > 0 else None),
            ("shares outstanding", shares if shares and shares > 0 else None),
            ("cash", cash),
            ("total debt", debt),
            ("stockholders' equity", equity),
            ("positive trailing-twelve-month revenue", revenue if revenue and revenue > 0 else None),
            ("trailing-twelve-month operating income", operating_margin),
        )
        if value is None
    ]
    if missing:
        result["reasons"] = [f"The model needs {label}." for label in missing]
        return result
    target = current_price * shares - cash + debt - equity_adjustment
    if target <= 0:
        result["reasons"] = ["Market equity value is at or below net cash, so the price implies nothing about operations."]
        return result

    invested_capital = equity + debt - cash
    uncapped = revenue / invested_capital if invested_capital > 0 else None
    sales_to_capital = min(uncapped, MAX_SALES_TO_CAPITAL) if uncapped is not None else MAX_SALES_TO_CAPITAL
    operations = {
        "revenue": revenue,
        "start_margin": operating_margin,
        "tax_rate": tax_rate,
        "terminal_growth": terminal_growth,
    }
    base = {**operations, "sales_to_capital": sales_to_capital}
    result["inputs"] = {
        "current_price": current_price,
        "target_enterprise_value": target,
        "revenue_ttm": revenue,
        "operating_margin_ttm": operating_margin,
        "tax_rate": tax_rate,
        "invested_capital": invested_capital,
        "sales_to_capital": sales_to_capital,
        "sales_to_capital_basis": "trailing_revenue_over_invested_capital"
        if uncapped is not None and uncapped <= MAX_SALES_TO_CAPITAL
        else "capped",
    }

    consensus_path = (
        consensus_growth_path(consensus["next_year_growth"], consensus["following_year_growth"], terminal_growth)
        if consensus
        else None
    )
    consensus_margin = consensus["operating_margin"] if consensus else None
    margins = [
        {"basis": basis, "target_margin": margin}
        for basis, margin in (
            ("trailing", operating_margin),
            ("consensus", consensus_margin),
            ("five_year_high", history.get("operating_margin_high")),
        )
        if margin is not None and margin > 0
    ]

    return_margin_basis = "consensus" if consensus_margin is not None else "trailing"
    return_margin = consensus_margin if consensus_margin is not None else operating_margin

    hurdles = []
    for required_return in REQUIRED_RETURNS:
        growth_by_margin = [
            {
                **margin,
                "revenue_growth": _solve(
                    lambda growth: enterprise_value(
                        **base, target_margin=margin["target_margin"],
                        growth_rates=growth_path(growth, terminal_growth), discount_rate=required_return,
                    ),
                    target, *GROWTH_RANGE,
                ),
            }
            for margin in margins
        ]
        hurdles.append({
            "required_return": required_return,
            "growth_by_margin": growth_by_margin,
            "margin_at_consensus_growth": _solve(
                lambda margin: enterprise_value(
                    **base, target_margin=margin, growth_rates=consensus_path, discount_rate=required_return,
                ),
                target, *MARGIN_RANGE,
            ) if consensus_path else None,
            # Revenue each dollar of invested capital must bring in, at consensus growth and margin.
            "sales_to_capital_at_consensus": _solve(
                lambda ratio: enterprise_value(
                    **operations, sales_to_capital=ratio, target_margin=return_margin,
                    growth_rates=consensus_path, discount_rate=required_return,
                ),
                target, *SALES_TO_CAPITAL_RANGE,
            ) if consensus_path else None,
        })
    result["hurdles"] = hurdles

    # The headline: what the price returns if consensus is met.
    implied_return = None
    return_status = "unavailable"
    if consensus_path:
        def value_at_return(rate: float) -> Optional[float]:
            return enterprise_value(
                **base, target_margin=return_margin, growth_rates=consensus_path, discount_rate=rate,
            )

        implied_return = _solve(value_at_return, target, MIN_RETURN, MAX_RETURN)
        lowest_rate_value = value_at_return(MIN_RETURN)
        if implied_return is not None:
            return_status = "solved"
        elif lowest_rate_value is not None and lowest_rate_value < target:
            # Even discounted at the lowest rate, consensus is worth less than the price.
            return_status = "below_range"
    result["implied_return"] = {
        "status": return_status,
        "rate": implied_return,
        "minimum_rate": MIN_RETURN,
        "margin_basis": return_margin_basis,
        "target_margin": return_margin,
        "growth_rates": consensus_path,
    }

    default_hurdle = next(item for item in hurdles if item["required_return"] == DEFAULT_REQUIRED_RETURN)
    default_growth = next(
        (item["revenue_growth"] for item in default_hurdle["growth_by_margin"] if item["basis"] == return_margin_basis),
        None,
    )
    result["assessment"] = {
        "return_vs_required": "below_required" if return_status == "below_range"
        else None if implied_return is None
        else "above_required" if implied_return >= DEFAULT_REQUIRED_RETURN else "below_required",
        "growth_margin_basis": return_margin_basis,
        "required_growth": default_growth,
        "required_growth_vs_references": _position(default_growth, [
            history.get("revenue_growth"),
            consensus["next_year_growth"] if consensus else None,
            consensus["following_year_growth"] if consensus else None,
        ]),
    }
    result["available"] = return_status != "unavailable" or any(
        item["revenue_growth"] is not None for hurdle in hurdles for item in hurdle["growth_by_margin"]
    )
    if not result["available"]:
        result["reasons"] = ["No revenue growth between -50% and 100% a year matches the price at these margins."]
    return result
