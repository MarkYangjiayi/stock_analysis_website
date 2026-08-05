from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    DailyPrice,
    DataPublication,
    FactorValue,
    FinancialStatement,
    PipelineRun,
    StockScreenerSnapshot,
    Ticker,
)
from services.personal_workspace import get_saved_valuation_scenarios
from services.security_master import canonicalize_ticker


DEFAULT_SCENARIOS: list[dict[str, Any]] = [
    {
        "scenario": "bear",
        "fcf_growth_rate": 0.05,
        "wacc": 0.105,
        "perpetual_growth": 0.02,
    },
    {
        "scenario": "base",
        "fcf_growth_rate": 0.10,
        "wacc": 0.09,
        "perpetual_growth": 0.025,
    },
    {
        "scenario": "bull",
        "fcf_growth_rate": 0.15,
        "wacc": 0.08,
        "perpetual_growth": 0.03,
    },
]

SCENARIO_NAMES = ("bear", "base", "bull")
VALUATION_MULTIPLES = {
    "pe_ratio",
    "forward_pe",
    "peg_ratio",
    "ps_ratio",
    "pb_ratio",
    "price_fcf",
    "ev_ebitda",
}

PEER_METRICS: tuple[dict[str, str], ...] = (
    {"key": "sales_growth_ttm", "label": "Sales growth (TTM)", "direction": "higher_better", "format": "percent"},
    {"key": "sales_growth_3yr", "label": "Sales growth (3Y)", "direction": "higher_better", "format": "percent"},
    {"key": "sales_growth_5yr", "label": "Sales growth (5Y)", "direction": "higher_better", "format": "percent"},
    {"key": "eps_growth_ttm", "label": "EPS growth (TTM)", "direction": "higher_better", "format": "percent"},
    {"key": "eps_growth_3yr", "label": "EPS growth (3Y)", "direction": "higher_better", "format": "percent"},
    {"key": "eps_growth_5yr", "label": "EPS growth (5Y)", "direction": "higher_better", "format": "percent"},
    {"key": "gross_margin", "label": "Gross margin", "direction": "higher_better", "format": "percent"},
    {"key": "operating_margin", "label": "Operating margin", "direction": "higher_better", "format": "percent"},
    {"key": "net_profit_margin", "label": "Net margin", "direction": "higher_better", "format": "percent"},
    {"key": "roe", "label": "Return on equity", "direction": "higher_better", "format": "percent"},
    {"key": "roa", "label": "Return on assets", "direction": "higher_better", "format": "percent"},
    {"key": "roic", "label": "Return on invested capital", "direction": "higher_better", "format": "percent"},
    {"key": "pe_ratio", "label": "P/E", "direction": "lower_better", "format": "multiple"},
    {"key": "forward_pe", "label": "Forward P/E", "direction": "lower_better", "format": "multiple"},
    {"key": "peg_ratio", "label": "PEG", "direction": "lower_better", "format": "multiple"},
    {"key": "ps_ratio", "label": "Price / sales", "direction": "lower_better", "format": "multiple"},
    {"key": "pb_ratio", "label": "Price / book", "direction": "lower_better", "format": "multiple"},
    {"key": "price_fcf", "label": "Price / FCF", "direction": "lower_better", "format": "multiple"},
    {"key": "ev_ebitda", "label": "EV / EBITDA", "direction": "lower_better", "format": "multiple"},
    {"key": "debt_to_equity", "label": "Debt / equity", "direction": "lower_better", "format": "ratio"},
)

WARNING_EVIDENCE_IDS = {
    "revenue_decline": "E27",
    "gross_margin_compression": "E28",
    "operating_margin_compression": "E29",
    "fcf_decline": "E30",
    "fcf_conversion": "E31",
    "debt_level": "E32",
    "debt_increase": "E33",
    "cash_decline": "E34",
    "share_dilution": "E35",
}


def _safe_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _copy_scenarios(scenarios: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "scenario": str(item["scenario"]).lower(),
            "fcf_growth_rate": float(item["fcf_growth_rate"]),
            "wacc": float(item["wacc"]),
            "perpetual_growth": float(item["perpetual_growth"]),
        }
        for item in scenarios
    ]


def validate_scenarios(
    scenarios: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate all three DCF scenarios and return them in Bear/Base/Bull order."""
    if len(scenarios) != 3:
        raise ValueError("Exactly three valuation scenarios are required.")

    try:
        normalized = _copy_scenarios(scenarios)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Each scenario requires scenario, fcf_growth_rate, wacc, and perpetual_growth.") from exc

    by_name = {item["scenario"]: item for item in normalized}
    if set(by_name) != set(SCENARIO_NAMES) or len(by_name) != 3:
        raise ValueError("Scenarios must contain Bear, Base, and Bull exactly once.")

    ordered = [by_name[name] for name in SCENARIO_NAMES]
    for item in ordered:
        name = item["scenario"].title()
        growth = item["fcf_growth_rate"]
        wacc = item["wacc"]
        terminal = item["perpetual_growth"]
        if not -0.20 <= growth <= 0.50:
            raise ValueError(f"{name} FCF growth must be between -20% and 50%.")
        if not 0.03 <= wacc <= 0.25:
            raise ValueError(f"{name} WACC must be between 3% and 25%.")
        if not -0.02 <= terminal <= 0.06:
            raise ValueError(f"{name} terminal growth must be between -2% and 6%.")
        if wacc - terminal < 0.005 - 1e-12:
            raise ValueError(
                f"{name} WACC must exceed terminal growth by at least 0.5 percentage points."
            )

    bear, base, bull = ordered
    if not (
        bear["fcf_growth_rate"]
        <= base["fcf_growth_rate"]
        <= bull["fcf_growth_rate"]
    ):
        raise ValueError("FCF growth must be ordered Bear <= Base <= Bull.")
    if not (
        bear["perpetual_growth"]
        <= base["perpetual_growth"]
        <= bull["perpetual_growth"]
    ):
        raise ValueError("Terminal growth must be ordered Bear <= Base <= Bull.")
    if not bear["wacc"] >= base["wacc"] >= bull["wacc"]:
        raise ValueError("WACC must be ordered Bear >= Base >= Bull.")
    return ordered


def valuation_unavailable_reasons(inputs: dict[str, Any]) -> list[str]:
    labels = {
        "fcf": "Free cash flow is unavailable.",
        "shares": "Shares outstanding are unavailable.",
        "cash": "Cash and short-term investments are unavailable.",
        "debt": "Total debt is unavailable.",
    }
    reasons = [labels[key] for key in labels if _safe_float(inputs.get(key)) is None]
    fcf = _safe_float(inputs.get("fcf"))
    shares = _safe_float(inputs.get("shares"))
    if fcf is not None and fcf <= 0:
        reasons.append("Free cash flow must be positive for this DCF.")
    if shares is not None and shares <= 0:
        reasons.append("Shares outstanding must be positive for a per-share valuation.")
    return reasons


def calculate_dcf_value(
    *,
    fcf: float,
    cash: float,
    debt: float,
    shares: float,
    fcf_growth_rate: float,
    wacc: float,
    perpetual_growth: float,
) -> dict[str, Any]:
    projected_fcf: list[float] = []
    present_value_fcf = 0.0
    current_fcf = fcf
    for year in range(1, 6):
        current_fcf *= 1 + fcf_growth_rate
        projected_fcf.append(current_fcf)
        present_value_fcf += current_fcf / ((1 + wacc) ** year)

    terminal_value = projected_fcf[-1] * (1 + perpetual_growth) / (
        wacc - perpetual_growth
    )
    present_value_terminal = terminal_value / ((1 + wacc) ** 5)
    enterprise_value = present_value_fcf + present_value_terminal
    equity_value = enterprise_value + cash - debt
    return {
        "intrinsic_value_per_share": equity_value / shares,
        "enterprise_value": enterprise_value,
        "equity_value": equity_value,
        "projected_fcf": projected_fcf,
        "present_value_explicit_fcf": present_value_fcf,
        "present_value_terminal": present_value_terminal,
    }


def _scenario_result(
    scenario: dict[str, Any],
    inputs: dict[str, Any],
    current_price: float | None,
) -> dict[str, Any]:
    assumptions = dict(scenario)
    result = {"scenario": scenario["scenario"], "assumptions": assumptions}
    reasons = valuation_unavailable_reasons(inputs)
    if reasons:
        return {**result, "available": False, "reasons": reasons}

    calculated = calculate_dcf_value(
        fcf=float(inputs["fcf"]),
        cash=float(inputs["cash"]),
        debt=float(inputs["debt"]),
        shares=float(inputs["shares"]),
        fcf_growth_rate=scenario["fcf_growth_rate"],
        wacc=scenario["wacc"],
        perpetual_growth=scenario["perpetual_growth"],
    )
    intrinsic = calculated["intrinsic_value_per_share"]
    margin = None
    if current_price is not None and current_price > 0:
        margin = intrinsic / current_price - 1
    return {
        **result,
        "available": True,
        **calculated,
        "upside_downside": margin,
    }


def _sensitivity_matrix(
    base: dict[str, Any],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    growth_values = [base["fcf_growth_rate"] + delta for delta in (-0.10, -0.05, 0, 0.05, 0.10)]
    wacc_values = [base["wacc"] + delta for delta in (-0.02, -0.01, 0, 0.01, 0.02)]
    matrix: list[list[float | None]] = []
    cell_reasons: list[list[str | None]] = []
    unavailable = valuation_unavailable_reasons(inputs)
    for growth in growth_values:
        row: list[float | None] = []
        reason_row: list[str | None] = []
        for wacc in wacc_values:
            reason = None
            if unavailable:
                reason = "; ".join(unavailable)
            elif not -0.20 <= growth <= 0.50:
                reason = "Growth is outside the allowed -20% to 50% range."
            elif not 0.03 <= wacc <= 0.25:
                reason = "WACC is outside the allowed 3% to 25% range."
            elif wacc - base["perpetual_growth"] < 0.005 - 1e-12:
                reason = "WACC must exceed terminal growth by at least 0.5 points."

            if reason:
                row.append(None)
                reason_row.append(reason)
                continue
            calculated = calculate_dcf_value(
                fcf=float(inputs["fcf"]),
                cash=float(inputs["cash"]),
                debt=float(inputs["debt"]),
                shares=float(inputs["shares"]),
                fcf_growth_rate=growth,
                wacc=wacc,
                perpetual_growth=base["perpetual_growth"],
            )
            row.append(calculated["intrinsic_value_per_share"])
            reason_row.append(None)
        matrix.append(row)
        cell_reasons.append(reason_row)

    return {
        "growth_values": growth_values,
        "wacc_values": wacc_values,
        "terminal_growth": base["perpetual_growth"],
        "values": matrix,
        "cell_reasons": cell_reasons,
    }


def _valuation_position(
    current_price: float | None,
    scenario_results: Sequence[dict[str, Any]],
) -> dict[str, str]:
    if current_price is None or current_price <= 0:
        return {
            "status": "unavailable",
            "text": "Valuation position is unavailable because the current price is missing.",
        }
    if any(not item.get("available") for item in scenario_results):
        reasons = next(item["reasons"] for item in scenario_results if not item.get("available"))
        return {"status": "unavailable", "text": f"Valuation unavailable: {' '.join(reasons)}"}

    values = {
        item["scenario"]: item["intrinsic_value_per_share"]
        for item in scenario_results
    }
    if current_price < values["bear"]:
        status = "below_bear"
        text = "Price is below the Bear-case intrinsic value."
    elif current_price < values["base"]:
        status = "between_bear_base"
        text = "Price is between the Bear- and Base-case intrinsic values."
    elif current_price <= values["bull"]:
        status = "between_base_bull"
        text = "Price is between the Base- and Bull-case intrinsic values."
    else:
        status = "above_bull"
        text = "Price is above the Bull-case intrinsic value."
    return {"status": status, "text": text}


def calculate_valuation(
    inputs: dict[str, Any],
    scenarios: Sequence[dict[str, Any]],
    current_price: float | None,
) -> dict[str, Any]:
    ordered = validate_scenarios(scenarios)
    results = [_scenario_result(item, inputs, current_price) for item in ordered]
    return {
        "available": all(item["available"] for item in results),
        "unavailable_reasons": valuation_unavailable_reasons(inputs),
        "inputs": inputs,
        "current_price": current_price,
        "scenarios": results,
        "position": _valuation_position(current_price, results),
        "sensitivity": _sensitivity_matrix(ordered[1], inputs),
        "formula": {
            "forecast_years": 5,
            "cash_treatment": "added",
            "debt_treatment": "deducted",
            "terminal_value": "FCF5 × (1 + terminal growth) / (WACC - terminal growth)",
        },
    }


def midrank_percentile(value: float, values: Iterable[float]) -> float:
    valid = [float(item) for item in values if _safe_float(item) is not None]
    if not valid:
        raise ValueError("A percentile requires at least one valid observation.")
    less = sum(item < value for item in valid)
    equal = sum(item == value for item in valid)
    return 100.0 * (less + 0.5 * equal) / len(valid)


def _valid_peer_value(metric_key: str, value: Any) -> float | None:
    number = _safe_float(value)
    if number is None:
        return None
    if metric_key in VALUATION_MULTIPLES and number <= 0:
        return None
    if metric_key == "debt_to_equity" and number < 0:
        return None
    return number


def _peer_scope_result(
    metric: dict[str, str],
    target_value: float | None,
    peers: Sequence[StockScreenerSnapshot],
    minimum_observations: int,
    scope: str,
) -> dict[str, Any]:
    values = [
        value
        for row in peers
        if (value := _valid_peer_value(metric["key"], getattr(row, metric["key"]))) is not None
    ]
    result: dict[str, Any] = {
        "scope": scope,
        "minimum_observations": minimum_observations,
        "observation_count": len(values),
        "available": False,
        "raw_percentile": None,
        "desirability_percentile": None,
        "reason": None,
    }
    if target_value is None:
        result["reason"] = "The ticker does not have a valid value for this metric."
        return result
    if len(values) < minimum_observations:
        result["reason"] = (
            f"{scope.title()} comparison requires {minimum_observations} valid observations; "
            f"{len(values)} are available."
        )
        return result

    raw = midrank_percentile(target_value, values)
    result.update(
        {
            "available": True,
            "raw_percentile": raw,
            "desirability_percentile": raw
            if metric["direction"] == "higher_better"
            else 100 - raw,
        }
    )
    return result


def build_peer_comparison(
    target: StockScreenerSnapshot | None,
    snapshot_rows: Sequence[StockScreenerSnapshot],
    *,
    fallback_sector: str | None = None,
    fallback_industry: str | None = None,
) -> dict[str, Any]:
    sector = target.sector if target and target.sector else fallback_sector
    industry = target.industry if target and target.industry else fallback_industry
    industry_rows = [row for row in snapshot_rows if industry and row.industry == industry]
    sector_rows = [row for row in snapshot_rows if sector and row.sector == sector]

    metrics: list[dict[str, Any]] = []
    for index, metric in enumerate(PEER_METRICS, start=7):
        target_value = (
            _valid_peer_value(metric["key"], getattr(target, metric["key"]))
            if target is not None
            else None
        )
        industry_result = _peer_scope_result(
            metric, target_value, industry_rows, 10, "industry"
        )
        sector_result = _peer_scope_result(
            metric, target_value, sector_rows, 20, "sector"
        )
        chosen = industry_result if industry_result["available"] else sector_result
        comparison_scope = chosen["scope"] if chosen["available"] else None
        metrics.append(
            {
                **metric,
                "evidence_id": f"E{index}",
                "value": target_value,
                "industry": industry_result,
                "sector": sector_result,
                "summary_scope": comparison_scope,
                "summary_percentile": chosen["desirability_percentile"]
                if chosen["available"]
                else None,
            }
        )

    available = [item for item in metrics if item["summary_percentile"] is not None]
    strongest = sorted(
        available,
        key=lambda item: (-item["summary_percentile"], item["key"]),
    )[:3]
    weakest = sorted(
        available,
        key=lambda item: (item["summary_percentile"], item["key"]),
    )[:3]
    return {
        "ticker_in_screener": target is not None,
        "industry": industry,
        "sector": sector,
        "industry_member_count": len(industry_rows),
        "sector_member_count": len(sector_rows),
        "metrics": metrics,
        "strongest": [_summary_metric(item) for item in strongest],
        "weakest": [_summary_metric(item) for item in weakest],
        "available_metric_count": len(available),
        "total_metric_count": len(PEER_METRICS),
    }


def _summary_metric(metric: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": metric["key"],
        "label": metric["label"],
        "value": metric["value"],
        "format": metric["format"],
        "direction": metric["direction"],
        "scope": metric["summary_scope"],
        "desirability_percentile": metric["summary_percentile"],
        "evidence_id": metric["evidence_id"],
    }


def _first_value(mapping: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _safe_float(mapping.get(key))
        if value is not None:
            return value
    return None


def _statement_point(record: FinancialStatement) -> dict[str, Any]:
    income = record.income_statement or {}
    balance = record.balance_sheet or {}
    cash_flow = record.cash_flow or {}
    debt = _first_value(balance, "shortLongTermDebtTotal", "totalDebt")
    if debt is None:
        short_debt = _first_value(balance, "shortTermDebt", "shortTermDebtTotal")
        long_debt = _first_value(balance, "longTermDebt", "longTermDebtTotal")
        if short_debt is not None or long_debt is not None:
            debt = (short_debt or 0) + (long_debt or 0)
    return {
        "date": record.fiscal_date,
        "revenue": _first_value(income, "totalRevenue")
        if income.get("totalRevenue") is not None
        else _safe_float(record.revenue),
        "gross_profit": _first_value(income, "grossProfit"),
        "operating_income": _first_value(income, "operatingIncome"),
        "net_income": _first_value(income, "netIncome")
        if income.get("netIncome") is not None
        else _safe_float(record.net_income),
        "fcf": _first_value(cash_flow, "freeCashFlow"),
        "cash_and_short_term_investments": _first_value(
            balance,
            "cashAndShortTermInvestments",
        ),
        "cash": _first_value(
            balance,
            "cashAndShortTermInvestments",
            "cashAndCashEquivalents",
            "cashAndEquivalents",
            "cash",
        ),
        "debt": debt,
        "equity": _first_value(balance, "totalStockholderEquity", "totalShareholderEquity"),
        "shares": _first_value(
            balance,
            "commonStockSharesOutstanding",
            "sharesOutstanding",
        ),
    }


def _quarter_index(value: date) -> int:
    return value.year * 4 + (value.month - 1) // 3


def _window_is_contiguous(points: Sequence[dict[str, Any]]) -> bool:
    if len(points) != 4:
        return False
    return all(
        _quarter_index(newer["date"]) - _quarter_index(older["date"]) == 1
        and 60 <= (newer["date"] - older["date"]).days <= 130
        for newer, older in zip(points, points[1:])
    )


def _sum_complete(points: Sequence[dict[str, Any]], key: str) -> float | None:
    values = [_safe_float(point.get(key)) for point in points]
    return sum(values) if values and all(value is not None for value in values) else None


def build_financial_context(
    records: Sequence[FinancialStatement],
) -> dict[str, Any]:
    points = [_statement_point(record) for record in sorted(records, key=lambda row: row.fiscal_date, reverse=True)[:8]]
    current_points = points[:4]
    previous_points = points[4:8]
    current_contiguous = _window_is_contiguous(current_points)
    previous_contiguous = _window_is_contiguous(previous_points)
    comparison_windows_are_adjacent = bool(
        current_contiguous
        and previous_contiguous
        and _quarter_index(current_points[-1]["date"])
        - _quarter_index(previous_points[0]["date"])
        == 1
        and 60
        <= (current_points[-1]["date"] - previous_points[0]["date"]).days
        <= 130
    )

    data_quality_notes: list[dict[str, str]] = []
    if len(points) < 8:
        data_quality_notes.append(
            {
                "code": "insufficient_quarterly_history",
                "message": f"Eight quarterly statements are required for YoY warning comparisons; {len(points)} are available.",
            }
        )
    if len(current_points) == 4 and not current_contiguous:
        data_quality_notes.append(
            {
                "code": "non_contiguous_current_ttm",
                "message": "The latest four statements are not contiguous quarters, so current TTM comparisons are unavailable.",
            }
        )
    if len(previous_points) == 4 and (
        not previous_contiguous or not comparison_windows_are_adjacent
    ):
        data_quality_notes.append(
            {
                "code": "non_contiguous_previous_ttm",
                "message": "The comparison statements do not form the four quarters immediately before current TTM, so prior-TTM comparisons are unavailable.",
            }
        )

    def window(points_in_window: Sequence[dict[str, Any]], contiguous: bool) -> dict[str, float | None]:
        if not contiguous:
            return {key: None for key in ("revenue", "gross_profit", "operating_income", "net_income", "fcf", "gross_margin", "operating_margin")}
        revenue = _sum_complete(points_in_window, "revenue")
        gross_profit = _sum_complete(points_in_window, "gross_profit")
        operating_income = _sum_complete(points_in_window, "operating_income")
        result = {
            "revenue": revenue,
            "gross_profit": gross_profit,
            "operating_income": operating_income,
            "net_income": _sum_complete(points_in_window, "net_income"),
            "fcf": _sum_complete(points_in_window, "fcf"),
            "gross_margin": gross_profit / revenue
            if revenue is not None and revenue > 0 and gross_profit is not None
            else None,
            "operating_margin": operating_income / revenue
            if revenue is not None and revenue > 0 and operating_income is not None
            else None,
        }
        return result

    current = window(current_points, current_contiguous)
    previous = window(previous_points, comparison_windows_are_adjacent)
    latest = points[0] if points else {}
    prior_year = points[4] if len(points) >= 5 else {}
    current_de = (
        latest["debt"] / latest["equity"]
        if latest.get("debt") is not None
        and latest["debt"] >= 0
        and latest.get("equity")
        and latest["equity"] > 0
        else None
    )
    previous_de = (
        prior_year["debt"] / prior_year["equity"]
        if prior_year.get("debt") is not None
        and prior_year["debt"] >= 0
        and prior_year.get("equity")
        and prior_year["equity"] > 0
        else None
    )
    return {
        "statement_count": len(points),
        "latest_statement_date": latest.get("date"),
        "points": points,
        "current_ttm": current,
        "previous_ttm": previous,
        "latest_balance": {
            "cash": latest.get("cash"),
            "cash_and_short_term_investments": latest.get("cash_and_short_term_investments"),
            "debt": latest.get("debt"),
            "equity": latest.get("equity"),
            "shares": latest.get("shares"),
            "debt_to_equity": current_de,
        },
        "prior_year_balance": {
            "cash": prior_year.get("cash"),
            "cash_and_short_term_investments": prior_year.get("cash_and_short_term_investments"),
            "debt": prior_year.get("debt"),
            "equity": prior_year.get("equity"),
            "shares": prior_year.get("shares"),
            "debt_to_equity": previous_de,
        },
        "data_quality_notes": data_quality_notes,
    }


def _decline(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous <= 0:
        return None
    return 1 - current / previous


def _increase(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous <= 0:
        return None
    return current / previous - 1


def _risk(
    rule_id: str,
    severity: str,
    title: str,
    message: str,
    metric: str,
    current: float | None,
    previous: float | None,
    evidence_metric: str,
) -> dict[str, Any]:
    return {
        "id": rule_id,
        "severity": severity,
        "title": title,
        "message": message,
        "metric": metric,
        "current": current,
        "previous": previous,
        "evidence_metric": evidence_metric,
        "evidence_id": WARNING_EVIDENCE_IDS[rule_id],
    }


def evaluate_fundamental_warnings(financial: dict[str, Any]) -> dict[str, Any]:
    current = financial["current_ttm"]
    previous = financial["previous_ttm"]
    latest = financial["latest_balance"]
    prior = financial["prior_year_balance"]
    warnings: list[dict[str, Any]] = []
    notes = list(financial.get("data_quality_notes", []))

    revenue_decline = _decline(current.get("revenue"), previous.get("revenue"))
    if revenue_decline is not None and revenue_decline >= 0.05:
        severity = "high" if revenue_decline >= 0.15 else "warning"
        warnings.append(_risk("revenue_decline", severity, "TTM revenue decline", f"TTM revenue declined {revenue_decline:.1%} year over year.", "revenue_change", current.get("revenue"), previous.get("revenue"), "revenue"))
    elif current.get("revenue") is None or previous.get("revenue") is None or (previous.get("revenue") or 0) <= 0:
        notes.append({"code": "revenue_comparison_unavailable", "message": "Revenue decline could not be assessed from two positive, complete TTM periods."})

    for rule_id, key, title, evidence_metric in (
        ("gross_margin_compression", "gross_margin", "Gross-margin compression", "gross_margin"),
        ("operating_margin_compression", "operating_margin", "Operating-margin compression", "operating_margin"),
    ):
        current_margin = current.get(key)
        previous_margin = previous.get(key)
        compression = (
            previous_margin - current_margin
            if current_margin is not None and previous_margin is not None
            else None
        )
        if compression is not None and compression >= 0.03:
            severity = "high" if compression >= 0.08 else "warning"
            warnings.append(_risk(rule_id, severity, title, f"{title.replace('-', ' ')} was {compression * 100:.1f} percentage points year over year.", "margin_compression", current_margin, previous_margin, evidence_metric))
        elif compression is None:
            notes.append({"code": f"{key}_comparison_unavailable", "message": f"{title} could not be assessed because one TTM margin is missing."})

    current_fcf = current.get("fcf")
    previous_fcf = previous.get("fcf")
    if current_fcf is not None and current_fcf < 0:
        warnings.append(_risk("fcf_decline", "high", "Negative free cash flow", "TTM free cash flow is negative.", "fcf", current_fcf, previous_fcf, "fcf"))
    else:
        fcf_decline = _decline(current_fcf, previous_fcf)
        if fcf_decline is not None and fcf_decline >= 0.30:
            severity = "high" if fcf_decline >= 0.60 else "warning"
            warnings.append(_risk("fcf_decline", severity, "TTM free-cash-flow decline", f"TTM free cash flow declined {fcf_decline:.1%} year over year.", "fcf_change", current_fcf, previous_fcf, "fcf"))
        elif current_fcf is None or previous_fcf is None or previous_fcf <= 0:
            notes.append({"code": "fcf_comparison_unavailable", "message": "FCF decline could not be assessed from two complete periods with positive prior-TTM FCF."})

    net_income = current.get("net_income")
    conversion = (
        current_fcf / net_income
        if current_fcf is not None and net_income is not None and net_income > 0
        else None
    )
    if conversion is not None and conversion < 0.50:
        severity = "high" if conversion < 0.25 else "warning"
        warnings.append(_risk("fcf_conversion", severity, "Weak cash conversion", f"TTM FCF / net income conversion is {conversion:.1%}.", "fcf_net_income_conversion", conversion, None, "fcf"))
    elif conversion is None:
        notes.append({"code": "fcf_conversion_unavailable", "message": "FCF conversion could not be assessed because FCF is missing or net income is non-positive."})

    current_de = latest.get("debt_to_equity")
    previous_de = prior.get("debt_to_equity")
    if current_de is not None and current_de > 1.5:
        severity = "high" if current_de > 3.0 else "warning"
        warnings.append(_risk("debt_level", severity, "Elevated debt / equity", f"Debt / equity is {current_de:.2f}x.", "debt_to_equity", current_de, previous_de, "debt"))
    elif current_de is None:
        notes.append({"code": "debt_level_unavailable", "message": "Debt / equity could not be assessed because debt or positive equity is unavailable."})

    debt_increase = _increase(current_de, previous_de)
    if current_de is not None and current_de > 0.5 and debt_increase is not None and debt_increase > 0.50:
        warnings.append(_risk("debt_increase", "warning", "Debt / equity rising quickly", f"Debt / equity rose {debt_increase:.1%} year over year to {current_de:.2f}x.", "debt_to_equity_change", current_de, previous_de, "debt"))
    elif current_de is None or previous_de is None or previous_de <= 0:
        notes.append({"code": "debt_trend_unavailable", "message": "The YoY debt / equity trend could not be assessed from two positive observations."})

    current_cash = latest.get("cash_and_short_term_investments")
    previous_cash = prior.get("cash_and_short_term_investments")
    cash_decline = _decline(current_cash, previous_cash)
    if cash_decline is not None and cash_decline >= 0.25:
        severity = "high" if cash_decline >= 0.50 else "warning"
        warnings.append(_risk("cash_decline", severity, "Cash balance decline", f"Cash and short-term investments declined {cash_decline:.1%} year over year.", "cash_change", current_cash, previous_cash, "cash"))
    elif current_cash is None or previous_cash is None or previous_cash <= 0:
        notes.append({"code": "cash_comparison_unavailable", "message": "Cash decline could not be assessed from two observations with positive prior-year cash."})

    dilution = _increase(latest.get("shares"), prior.get("shares"))
    if dilution is not None and dilution >= 0.03:
        severity = "high" if dilution >= 0.10 else "warning"
        warnings.append(_risk("share_dilution", severity, "Share dilution", f"Shares outstanding increased {dilution:.1%} year over year.", "shares_change", latest.get("shares"), prior.get("shares"), "shares"))
    elif latest.get("shares") is None or prior.get("shares") is None or (prior.get("shares") or 0) <= 0:
        notes.append({"code": "shares_comparison_unavailable", "message": "Share dilution could not be assessed from two observations with positive prior-year shares."})

    unique_notes = list({(item["code"], item["message"]): item for item in notes}.values())
    warnings.sort(key=lambda item: (0 if item["severity"] == "high" else 1, item["id"]))
    return {
        "warnings": warnings,
        "data_quality_notes": unique_notes,
        "high_count": sum(item["severity"] == "high" for item in warnings),
        "warning_count": sum(item["severity"] == "warning" for item in warnings),
    }


async def _latest_publication(db: AsyncSession, dataset: str) -> DataPublication | None:
    result = await db.execute(
        select(DataPublication)
        .join(PipelineRun, PipelineRun.id == DataPublication.pipeline_run_id)
        .where(
            DataPublication.dataset == dataset,
            DataPublication.status == "published",
            PipelineRun.status == "published",
        )
        .order_by(DataPublication.as_of_date.desc(), DataPublication.published_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _load_factor_snapshot(
    db: AsyncSession,
    ticker: str,
) -> dict[str, Any]:
    publication = await _latest_publication(db, "factors")
    if publication is None:
        return {"as_of_date": None, "published_at": None, "version": None, "factors": {}}
    result = await db.execute(
        select(FactorValue).where(
            FactorValue.ticker == ticker,
            FactorValue.as_of_date == publication.as_of_date,
            FactorValue.source_run_id == publication.pipeline_run_id,
        )
    )
    rows = list(result.scalars().all())
    if not rows:
        return {
            "as_of_date": publication.as_of_date,
            "published_at": publication.published_at,
            "version": None,
            "factors": {},
        }
    versions: dict[str, list[FactorValue]] = {}
    for row in rows:
        versions.setdefault(row.version, []).append(row)
    version = max(versions, key=lambda key: (len(versions[key]), key))
    return {
        "as_of_date": publication.as_of_date,
        "published_at": publication.published_at,
        "version": version,
        "factors": {
            row.factor_name: {
                "raw_value": row.raw_value,
                "normalized_value": row.normalized_value,
                "details": row.details,
            }
            for row in versions[version]
        },
    }


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else None


def _evidence_items(
    *,
    price: float | None,
    price_date: date | None,
    screener_date: date | None,
    financial: dict[str, Any],
    valuation: dict[str, Any],
    peers: dict[str, Any],
    risks: dict[str, Any],
    factors: dict[str, Any],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = [
        {"id": "E1", "kind": "price", "label": "Current price", "value": price, "source_date": _iso(price_date), "available": price is not None},
        {"id": "E2", "kind": "screener", "label": "Published Screener membership", "value": {"ticker_in_screener": peers["ticker_in_screener"], "industry": peers["industry"], "sector": peers["sector"]}, "source_date": _iso(screener_date), "available": screener_date is not None},
        {"id": "E3", "kind": "financials", "label": "Quarterly financial coverage", "value": {"statement_count": financial["statement_count"], "current_ttm": financial["current_ttm"], "previous_ttm": financial["previous_ttm"], "latest_balance": financial["latest_balance"], "prior_year_balance": financial["prior_year_balance"], "data_quality_notes": risks["data_quality_notes"]}, "source_date": _iso(financial["latest_statement_date"]), "available": financial["statement_count"] > 0},
    ]
    for evidence_id, scenario in zip(("E4", "E5", "E6"), valuation["scenarios"]):
        items.append({"id": evidence_id, "kind": "valuation", "label": f"{scenario['scenario'].title()} DCF", "value": scenario, "source_date": _iso(financial["latest_statement_date"]), "available": scenario["available"]})
    for metric in peers["metrics"]:
        items.append({"id": metric["evidence_id"], "kind": "peer_metric", "label": metric["label"], "value": {"metric_value": metric["value"], "direction": metric["direction"], "industry": metric["industry"], "sector": metric["sector"], "summary_scope": metric["summary_scope"], "summary_percentile": metric["summary_percentile"]}, "source_date": _iso(screener_date), "available": metric["summary_percentile"] is not None})
    by_rule = {item["id"]: item for item in risks["warnings"]}
    for rule_id, evidence_id in WARNING_EVIDENCE_IDS.items():
        warning = by_rule.get(rule_id)
        items.append({"id": evidence_id, "kind": "fundamental_warning", "label": rule_id.replace("_", " ").title(), "value": warning or {"triggered": False, "assessment": "not triggered on available data", "data_quality_notes": risks["data_quality_notes"]}, "source_date": _iso(financial["latest_statement_date"]), "available": financial["statement_count"] > 0})
    items.append({"id": "E36", "kind": "published_factors", "label": "Published factors", "value": {"version": factors["version"], "factors": factors["factors"]}, "source_date": _iso(factors["as_of_date"]), "available": bool(factors["factors"])})
    return items


def _deterministic_summary(
    valuation: dict[str, Any],
    peers: dict[str, Any],
    risks: dict[str, Any],
    factors: dict[str, Any],
    financial: dict[str, Any],
) -> dict[str, Any]:
    missing_reasons = list(valuation["unavailable_reasons"])
    if not peers["ticker_in_screener"]:
        missing_reasons.append("Ticker is outside the latest published Screener universe.")
    if peers["available_metric_count"] < peers["total_metric_count"]:
        missing_reasons.append(
            f"Peer percentiles are available for {peers['available_metric_count']} of {peers['total_metric_count']} metrics."
        )
    if not factors["factors"]:
        missing_reasons.append("No published factor values are available for this ticker.")
    return {
        "valuation_position": valuation["position"],
        "strongest_peer_metrics": peers["strongest"],
        "weakest_peer_metrics": peers["weakest"],
        "fundamental_warnings": risks["warnings"],
        "coverage": {
            "quarterly_statements": financial["statement_count"],
            "peer_metrics_available": peers["available_metric_count"],
            "peer_metrics_total": peers["total_metric_count"],
            "published_factor_count": len(factors["factors"]),
            "missing_data_reasons": list(dict.fromkeys(missing_reasons)),
            "data_quality_notes": risks["data_quality_notes"],
        },
    }


async def _load_price_and_financial_context(
    ticker: str,
    db: AsyncSession,
) -> tuple[DailyPrice | None, float | None, dict[str, Any]]:
    price_result = await db.execute(
        select(DailyPrice)
        .where(DailyPrice.ticker == ticker)
        .order_by(DailyPrice.date.desc())
        .limit(1)
    )
    price_row = price_result.scalar_one_or_none()
    current_price = (
        _safe_float(price_row.adjusted_close)
        if price_row and price_row.adjusted_close is not None
        else _safe_float(price_row.close) if price_row else None
    )
    financial_result = await db.execute(
        select(FinancialStatement)
        .where(
            FinancialStatement.ticker == ticker,
            FinancialStatement.period == "Quarterly",
        )
        .order_by(FinancialStatement.fiscal_date.desc())
        .limit(8)
    )
    financial = build_financial_context(list(financial_result.scalars().all()))
    return price_row, current_price, financial


def _valuation_inputs(financial: dict[str, Any]) -> dict[str, Any]:
    return {
        "fcf": financial["current_ttm"]["fcf"],
        "cash": financial["latest_balance"]["cash"],
        "debt": financial["latest_balance"]["debt"],
        "shares": financial["latest_balance"]["shares"],
        "financial_statement_date": _iso(financial["latest_statement_date"]),
    }


async def calculate_ticker_valuation(
    ticker: str,
    db: AsyncSession,
    scenarios: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Calculate edited scenarios without loading peer or AI evidence."""
    canonical_ticker = canonicalize_ticker(ticker)
    _, current_price, financial = await _load_price_and_financial_context(
        canonical_ticker,
        db,
    )
    valuation = calculate_valuation(
        _valuation_inputs(financial),
        validate_scenarios(scenarios),
        current_price,
    )
    valuation["scenario_source"] = "request"
    return valuation


async def get_decision_support(
    ticker: str,
    db: AsyncSession,
    *,
    include_saved_scenarios: bool = False,
    scenarios: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    canonical_ticker = canonicalize_ticker(ticker)
    profile_result = await db.execute(select(Ticker).where(Ticker.ticker == canonical_ticker))
    profile = profile_result.scalar_one_or_none()

    price_row, current_price, financial = await _load_price_and_financial_context(
        canonical_ticker,
        db,
    )

    screener_publication = await _latest_publication(db, "screener")
    snapshot_rows: list[StockScreenerSnapshot] = []
    if screener_publication is not None:
        snapshots_result = await db.execute(
            select(StockScreenerSnapshot).where(
                StockScreenerSnapshot.date == screener_publication.as_of_date
            )
        )
        snapshot_rows = list(snapshots_result.scalars().all())
    target_snapshot = next(
        (row for row in snapshot_rows if row.ticker == canonical_ticker),
        None,
    )
    peers = build_peer_comparison(
        target_snapshot,
        snapshot_rows,
        fallback_sector=profile.sector if profile else None,
        fallback_industry=profile.industry if profile else None,
    )
    factors = await _load_factor_snapshot(db, canonical_ticker)

    scenario_source = "request"
    selected_scenarios = scenarios
    if selected_scenarios is None and include_saved_scenarios:
        selected_scenarios = await get_saved_valuation_scenarios(db, canonical_ticker)
        scenario_source = "saved" if selected_scenarios is not None else "default"
    elif selected_scenarios is None:
        scenario_source = "default"
    if selected_scenarios is None:
        selected_scenarios = DEFAULT_SCENARIOS
    selected_scenarios = validate_scenarios(selected_scenarios)

    valuation = calculate_valuation(
        _valuation_inputs(financial),
        selected_scenarios,
        current_price,
    )
    valuation["scenario_source"] = scenario_source
    risks = evaluate_fundamental_warnings(financial)
    evidence = _evidence_items(
        price=current_price,
        price_date=price_row.date if price_row else None,
        screener_date=screener_publication.as_of_date if screener_publication else None,
        financial=financial,
        valuation=valuation,
        peers=peers,
        risks=risks,
        factors=factors,
    )
    summary = _deterministic_summary(valuation, peers, risks, factors, financial)
    return {
        "metadata": {
            "ticker": canonical_ticker,
            "company_name": profile.name if profile else (target_snapshot.name if target_snapshot else None),
            "industry": peers["industry"],
            "sector": peers["sector"],
            "price_date": _iso(price_row.date if price_row else None),
            "screener_date": _iso(screener_publication.as_of_date if screener_publication else None),
            "screener_published_at": _iso(screener_publication.published_at if screener_publication else None),
            "financial_statement_date": _iso(financial["latest_statement_date"]),
            "factor_date": _iso(factors["as_of_date"]),
            "factor_published_at": _iso(factors["published_at"]),
        },
        "summary": summary,
        "valuation": valuation,
        "peer_comparison": peers,
        "risks": risks,
        "published_factors": {
            **factors,
            "as_of_date": _iso(factors["as_of_date"]),
            "published_at": _iso(factors["published_at"]),
        },
        "evidence": evidence,
    }
