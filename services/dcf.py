"""Pure valuation kernel; every API uses the same version and cash-flow rules."""
from __future__ import annotations

import math
from typing import Any, Sequence

MODEL_VERSION = "fcff-2.0"
FORECAST_YEARS = 10
INITIAL_GROWTH_YEARS = 5


def calculate_dcf_value(
    *, fcf: float, cash: float, debt: float, shares: float,
    fcf_growth_rate: float, wacc: float, perpetual_growth: float,
    forecast_years: int = FORECAST_YEARS,
    initial_growth_years: int = INITIAL_GROWTH_YEARS,
    explicit_fcff: Sequence[float] | None = None,
    terminal_fcff: float | None = None,
    equity_adjustment: float = 0,
) -> dict[str, Any]:
    values = (fcf, cash, debt, shares, fcf_growth_rate, wacc, perpetual_growth, equity_adjustment)
    if any(not math.isfinite(float(v)) for v in values):
        raise ValueError("DCF inputs must be finite.")
    if shares <= 0 or debt < 0 or cash < 0:
        raise ValueError("Positive shares and nonnegative cash/debt are required.")
    if wacc <= perpetual_growth or wacc <= -1:
        raise ValueError("Discount rate must exceed terminal growth.")
    if not 1 <= initial_growth_years <= forecast_years <= 30:
        raise ValueError("Invalid initial/fade forecast horizon.")
    growth_rates: list[float | None] = []
    if explicit_fcff is not None:
        projected = [float(v) for v in explicit_fcff]
        if len(projected) != forecast_years or any(not math.isfinite(v) for v in projected):
            raise ValueError("The explicit forecast must contain one finite cash flow per year.")
        growth_rates = [None] * forecast_years
    else:
        if fcf <= 0:
            raise ValueError("Nonpositive starting FCFF requires an explicit operating forecast.")
        projected = []
        current = fcf
        for year in range(1, forecast_years + 1):
            progress = max(0, year - initial_growth_years) / max(1, forecast_years - initial_growth_years)
            growth = fcf_growth_rate + (perpetual_growth - fcf_growth_rate) * progress
            current *= 1 + growth
            projected.append(current)
            growth_rates.append(growth)
    next_fcf = projected[-1] * (1 + perpetual_growth) if terminal_fcff is None else float(terminal_fcff)
    if not math.isfinite(next_fcf) or next_fcf <= 0:
        raise ValueError("Sustainable positive terminal FCFF is required.")
    explicit_pv = sum(v / (1 + wacc) ** year for year, v in enumerate(projected, 1))
    terminal_value = next_fcf / (wacc - perpetual_growth)
    terminal_pv = terminal_value / (1 + wacc) ** forecast_years
    enterprise_value = explicit_pv + terminal_pv
    equity_value = enterprise_value + cash - debt + equity_adjustment
    return {"model_version": MODEL_VERSION,
            "intrinsic_value_per_share": equity_value / shares,
            "enterprise_value": enterprise_value, "equity_value": equity_value,
            "projected_fcf": projected, "projected_growth_rates": growth_rates,
            "present_value_explicit_fcf": explicit_pv, "present_value_terminal": terminal_pv,
            "terminal_fcff": next_fcf, "terminal_value": terminal_value,
            "terminal_share_of_enterprise_value": terminal_pv / enterprise_value if enterprise_value > 0 else None,
            "forecast_years": forecast_years, "initial_growth_years": initial_growth_years,
            "equity_adjustment": equity_adjustment,
            "terminal_method": "perpetual_growth",
            "terminal_reinvestment_basis": "explicit_operating_forecast" if terminal_fcff is not None else "FCFF_continuation_reinvestment_not_independently_forecast"}


def operating_forecast_cash_flows(rows: Sequence[dict], terminal_growth: float, terminal_roic: float) -> tuple[list[float], float]:
    """Validate user-sourced operating forecasts, including negative early FCFF.

    Net reinvestment = capex - depreciation + change in working capital.
    The terminal period funds growth at the stated return on invested capital.
    """
    if len(rows) != FORECAST_YEARS:
        raise ValueError("An operating forecast requires ten annual rows.")
    if not math.isfinite(terminal_roic) or not 0 < terminal_roic <= 1 or terminal_growth >= terminal_roic:
        raise ValueError("Terminal ROIC must be positive, at most 100%, and exceed terminal growth.")
    result = []
    last_nopat = None
    for index, row in enumerate(rows, 1):
        if row.get("year") != index or not str(row.get("source") or "").strip():
            raise ValueError("Operating forecast years must be ordered 1–10 and each needs an assumption source.")
        try:
            revenue, margin, tax, capex, depreciation, working_capital = (
                float(row[k]) for k in ("revenue", "operating_margin", "tax_rate", "capex", "depreciation", "change_in_working_capital"))
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError("All operating-forecast inputs are required.") from exc
        if any(not math.isfinite(v) for v in (revenue, margin, tax, capex, depreciation, working_capital)):
            raise ValueError("Operating-forecast inputs must be finite.")
        if revenue <= 0 or capex < 0 or depreciation < 0 or not -1 <= margin <= 1 or not 0 <= tax <= 1:
            raise ValueError("Invalid revenue, margin, tax, capex or depreciation in operating forecast.")
        ebit = revenue * margin
        last_nopat = ebit - max(ebit, 0) * tax
        result.append(last_nopat - capex + depreciation - working_capital)
    # Negative growth does not assume perpetual asset liquidation.
    reinvestment_rate = max(terminal_growth, 0) / terminal_roic
    terminal = last_nopat * (1 + terminal_growth) * (1 - reinvestment_rate)
    if terminal <= 0:
        raise ValueError("Forecast does not support positive sustainable terminal cash flow.")
    return result, terminal
