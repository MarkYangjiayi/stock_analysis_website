"""Research checks that never use market/competitor prices as accuracy labels."""
from __future__ import annotations

from datetime import date
from statistics import mean, median
from typing import Any, Sequence

from services.valuation_inputs import number


def external_comparability(local: dict, external: dict) -> dict[str, Any]:
    reasons = []
    for key in ("currency", "cash_flow_type", "terminal_method", "share_basis", "quote_date", "model_as_of", "forecast_years", "discount_rate_type"):
        if not local.get(key) or not external.get(key):
            reasons.append(f"Missing {key}; synchronization is not established.")
        elif local[key] != external[key]:
            reasons.append(f"Different {key}.")
    if external.get("plausibility_flags"):
        reasons.append("External source has unresolved plausibility flags.")
    if not external.get("source_url"):
        reasons.append("External source URL is missing.")
    return {"same_basis": not reasons, "reasons": reasons, "use": "descriptive_reference_only", "accuracy_label": False}


def select_point_in_time(records: Sequence[dict], as_of: str) -> list[dict]:
    cutoff = date.fromisoformat(as_of)
    selected = {}
    for record in records:
        if record.get("availability_estimated") or not record.get("available_at"):
            continue
        try:
            available = date.fromisoformat(str(record["available_at"])[:10])
            end = date.fromisoformat(record["period_end"])
        except (TypeError, ValueError, KeyError):
            continue
        if available > cutoff or end > cutoff:
            continue
        key = (record["ticker"], record["period_end"], record["cash_flow_type"], record.get("currency", ""), record.get("unit", ""))
        rank = (str(record["available_at"]), int(record.get("revision", 0)))
        if key not in selected or rank > selected[key][0]:
            selected[key] = (rank, record)
    return [value[1] for _, value in sorted(selected.items())]


def score_realized_cash_flows(forecasts: Sequence[dict], actuals: Sequence[dict], evaluation_date: str) -> dict:
    """Match exact fiscal periods and definitions; retain zero/negative outcomes.

    Each prediction requires provenance for when every input was available.
    Current provider history with unknown revision availability is excluded.
    """
    outcomes = {(x["ticker"], x["period_end"], x["cash_flow_type"], x.get("currency"), x.get("unit")): x for x in select_point_in_time(actuals, evaluation_date)}
    errors, covered, exclusions = [], [], []
    for forecast in forecasts:
        as_of = forecast.get("forecast_as_of")
        dates = forecast.get("input_available_at")
        if not as_of or not dates or forecast.get("availability_estimated") or not forecast.get("currency") or not forecast.get("unit"):
            exclusions.append("Missing point-in-time forecast provenance.")
            continue
        try:
            forecast_date = date.fromisoformat(as_of)
            valid_dates = all(date.fromisoformat(str(v)[:10]) <= forecast_date for v in dates)
            valid_dates = valid_dates and forecast_date < date.fromisoformat(forecast["period_end"]) and forecast_date <= date.fromisoformat(evaluation_date)
        except (TypeError, ValueError, KeyError):
            valid_dates = False
        if not valid_dates:
            exclusions.append("Forecast contains unavailable inputs or does not precede the outcome.")
            continue
        key = (forecast["ticker"], forecast["period_end"], forecast["cash_flow_type"], forecast["currency"], forecast["unit"])
        actual = outcomes.get(key)
        prediction = number(forecast.get("base"))
        realized = number(actual.get("value")) if actual else None
        if prediction is None or realized is None:
            exclusions.append("Matching realized cash flow is not available.")
            continue
        errors.append(prediction - realized)
        lo, hi = number(forecast.get("bear")), number(forecast.get("bull"))
        if lo is not None and hi is not None and lo <= hi:
            covered.append(lo <= realized <= hi)
    return {"matched_count": len(errors), "mean_absolute_error": mean(abs(e) for e in errors) if errors else None,
            "median_signed_error": median(errors) if errors else None,
            "scenario_coverage": mean(covered) if covered else None, "coverage_count": len(covered),
            "exclusions": exclusions, "price_fitting_used": False,
            "note": "Scenario coverage is descriptive; cases are not calibrated probability intervals."}
