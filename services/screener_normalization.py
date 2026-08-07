"""Canonical value rules shared by screener ingestion, queries, and QA."""

from __future__ import annotations

import math
from typing import Any, MutableMapping, Optional


# EODHD uses this cap-like value when a sales multiple has no useful
# denominator. Treat it as unavailable instead of letting it rank as a real
# valuation observation.
PROVIDER_MULTIPLE_SENTINEL = 999_999.0

POSITIVE_MULTIPLE_FIELDS = frozenset({
    "pe_ratio",
    "forward_pe",
    "peg_ratio",
    "ps_ratio",
    "pb_ratio",
    "price_cash",
    "price_fcf",
    "ev_ebitda",
    "ev_sales",
})
SENTINEL_CAPPED_MULTIPLE_FIELDS = frozenset({"ps_ratio", "ev_sales"})
POSITIVE_VALUE_FIELDS = frozenset({
    "market_cap",
    "target_price",
    "shares_outstanding",
    "shares_float",
    "close",
    "ma20",
    "ma50",
    "ma200",
})
NONNEGATIVE_VALUE_FIELDS = frozenset({
    "dividend_yield",
    "short_float",
    "current_ratio",
    "quick_ratio",
    "lt_debt_to_equity",
    "debt_to_equity",
    "payout_ratio",
    "insider_ownership",
    "institutional_ownership",
    "average_volume_3m",
    "relative_volume",
    "volume",
    "volatility_1w",
    "volatility_1m",
    "atr_14",
})
RETURN_FIELDS = frozenset({
    "performance_1d",
    "performance_1w",
    "performance_1m",
    "performance_3m",
    "performance_6m",
    "performance_ytd",
    "performance_1yr",
    "gap",
    "change",
    "change_from_open",
})
HIGH_DISTANCE_FIELDS = frozenset({"high_20d_rel", "high_50d_rel", "high_52w_rel"})
LOW_DISTANCE_FIELDS = frozenset({"low_20d_rel", "low_50d_rel", "low_52w_rel"})

NON_PRIMARY_EXCHANGE_PREFIXES = ("OTC", "PINK", "GREY")
NON_PRIMARY_EXCHANGES = frozenset({"NQB", "PNK", "GREY MARKET"})


def finite_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def normalize_positive(value: Any) -> Optional[float]:
    result = finite_float(value)
    return result if result is not None and result > 0 else None


def normalize_nonnegative(value: Any) -> Optional[float]:
    result = finite_float(value)
    return result if result is not None and result >= 0 else None


def normalize_multiple(field_name: str, value: Any) -> Optional[float]:
    result = normalize_positive(value)
    if (
        result is not None
        and field_name in SENTINEL_CAPPED_MULTIPLE_FIELDS
        and result >= PROVIDER_MULTIPLE_SENTINEL
    ):
        return None
    return result


def normalize_analyst_recommendation(value: Any) -> Optional[float]:
    result = finite_float(value)
    return result if result is not None and 1 <= result <= 5 else None


def normalize_public_screener_values(
    values: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Normalize fields whose public screener semantics are unambiguous.

    Negative profitability, growth, cash flow, and beta values are deliberately
    preserved. The rules here only reject values that violate a metric's
    definition or would sort as a misleadingly cheap valuation.
    """
    invalid_earnings_base = (
        values.get("pe_ratio") is not None
        and normalize_multiple("pe_ratio", values.get("pe_ratio")) is None
    )
    invalid_book_base = (
        values.get("pb_ratio") is not None
        and normalize_multiple("pb_ratio", values.get("pb_ratio")) is None
    )
    invalid_sales_base = (
        values.get("ps_ratio") is not None
        and normalize_multiple("ps_ratio", values.get("ps_ratio")) is None
    )

    for field_name in POSITIVE_MULTIPLE_FIELDS:
        if field_name in values:
            values[field_name] = normalize_multiple(field_name, values[field_name])
    for field_name in POSITIVE_VALUE_FIELDS:
        if field_name in values:
            normalized = normalize_positive(values[field_name])
            values[field_name] = (
                int(normalized)
                if normalized is not None
                and field_name in {"shares_outstanding", "shares_float"}
                else normalized
            )
    for field_name in NONNEGATIVE_VALUE_FIELDS:
        if field_name in values:
            values[field_name] = normalize_nonnegative(values[field_name])

    if "analyst_recommendation" in values:
        values["analyst_recommendation"] = normalize_analyst_recommendation(
            values["analyst_recommendation"]
        )
    if "rsi_14" in values:
        rsi = finite_float(values["rsi_14"])
        values["rsi_14"] = rsi if rsi is not None and 0 <= rsi <= 100 else None
    for field_name in RETURN_FIELDS:
        if field_name in values:
            result = finite_float(values[field_name])
            values[field_name] = result if result is not None and result >= -1 else None
    for field_name in HIGH_DISTANCE_FIELDS:
        if field_name in values:
            result = finite_float(values[field_name])
            values[field_name] = (
                result if result is not None and -1 <= result <= 0 else None
            )
    for field_name in LOW_DISTANCE_FIELDS:
        if field_name in values:
            values[field_name] = normalize_nonnegative(values[field_name])
    if invalid_earnings_base and "payout_ratio" in values:
        values["payout_ratio"] = None
    if invalid_book_base and "roe" in values:
        values["roe"] = None
    if invalid_sales_base:
        for field_name in ("gross_margin", "operating_margin", "net_profit_margin"):
            if field_name in values:
                values[field_name] = None
    return values


def is_valid_public_screener_value(field_name: str, value: Any) -> bool:
    if value is None:
        return True
    normalized = {field_name: value}
    normalize_public_screener_values(normalized)
    return normalized[field_name] is not None


def is_non_primary_exchange(exchange: Any) -> bool:
    value = str(exchange or "").strip().upper()
    if not value:
        return False
    return value in NON_PRIMARY_EXCHANGES or value.startswith(
        NON_PRIMARY_EXCHANGE_PREFIXES
    )
