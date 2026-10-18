"""Consensus EPS revisions and short-interest signals from one fundamentals payload.

Both come from the EODHD fundamentals document the screener already fetches
(``Earnings.Trend`` and ``Technicals``), so they cost no extra provider calls.
They describe the provider's consensus and its latest short-interest report as
of the payload's ``General.UpdatedAt``. No point-in-time history exists behind
them, so they feed screening, risk flags and report tags only, never backtests.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from services.events_expectations import load_latest_fundamentals_snapshot

# Current fiscal year (FY1) consensus. The Trend map also keeps every earlier
# fiscal year under the same "0y" label, so the latest period end is current.
TREND_PERIOD = "0y"
# Next fiscal year. A one-off gain or charge moves only the current year, so a
# percentage revision counts only when the next year moves the same way.
NEXT_TREND_PERIOD = "+1y"
# A latest fiscal year that ended this long before the payload date means the
# provider stopped rolling the consensus forward (for example, after delisting).
MAX_FISCAL_YEAR_AGE_DAYS = 120
# Percentage revisions of near-zero EPS do not describe the size of a change.
MIN_EPS_BASE = 0.05
MIN_REVISION_ANALYSTS = 3
# Risk flags and report tags ignore payloads older than this.
MAX_SIGNAL_AGE_DAYS = 14

# (warning, high) thresholds.
ESTIMATE_CUT_30D = (-0.03, -0.10)
ESTIMATE_CUT_90D = (-0.075, -0.20)
ESTIMATE_RAISE_30D = 0.03
ESTIMATE_RAISE_90D = 0.075
# Net share of covering analysts revising in one direction over 30 days.
REVISION_BREADTH = 0.25
MIN_DIRECTIONAL_REVISIONS = 3
# Month-over-month rise in shares sold short, and the short share of float it
# needs before the rise matters.
SHORT_INTEREST_RISE = (0.25, 0.50)
SHORT_FLOAT_FOR_RISE = (0.05, 0.10)
SHORT_FLOAT_LEVEL = (0.15, 0.25)

SCREENER_FIELDS = (
    "eps_revision_30d",
    "eps_revision_90d",
    "eps_revision_breadth_30d",
    "eps_estimate_analysts",
    "short_interest_change",
    "short_ratio",
)
SIGNAL_KEYS = (
    "eps_period_end",
    "eps_estimate",
    "eps_estimate_30d_ago",
    "eps_estimate_90d_ago",
    "eps_revision_30d",
    "eps_revision_90d",
    "eps_revisions_up_30d",
    "eps_revisions_down_30d",
    "eps_estimate_analysts",
    "eps_revision_breadth_30d",
    "eps_next_year_revision_30d",
    "eps_next_year_revision_90d",
    "shares_short",
    "shares_short_prior_month",
    "short_interest_change",
    "short_float",
    "short_ratio",
)
SOURCE_LABEL = "EODHD fundamentals"


def _number(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _count(value: Any) -> Optional[int]:
    result = _number(value)
    if result is None or result < 0 or not result.is_integer():
        return None
    return int(result)


def _positive(value: Any) -> Optional[float]:
    result = _number(value)
    return result if result is not None and result > 0 else None


def _eps(value: Any) -> Optional[float]:
    # A consensus of exactly 0.0000 is a provider placeholder, not a forecast.
    result = _number(value)
    return result if result is not None and result != 0 else None


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _relative_change(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous is None or abs(previous) < MIN_EPS_BASE:
        return None
    return (current - previous) / abs(previous)


def _latest_trend_row(trend: Any, period: str, as_of: Optional[date]) -> Optional[tuple[date, dict]]:
    if isinstance(trend, dict):
        items = trend.items()
    elif isinstance(trend, list):
        items = ((None, row) for row in trend)
    else:
        return None
    rows = []
    for key, row in items:
        if not isinstance(row, dict) or str(row.get("period") or "").strip().lower() != period:
            continue
        period_end = _parse_date(row.get("date") or key)
        if period_end is not None:
            rows.append((period_end, row))
    if not rows:
        return None
    period_end, row = max(rows, key=lambda item: item[0])
    if as_of is not None and period_end < as_of - timedelta(days=MAX_FISCAL_YEAR_AGE_DAYS):
        return None
    return period_end, row


def extract_expectation_signals(
    payload: Any,
    *,
    fallback_as_of: Optional[date] = None,
) -> dict[str, Any]:
    """Return the raw signal values; every missing input stays ``None``.

    ``as_of`` is the provider's update date, or ``fallback_as_of`` (the fetch
    date) when the payload does not carry one.
    """
    payload = payload if isinstance(payload, dict) else {}
    general = payload.get("General") or {}
    earnings = payload.get("Earnings") or {}
    technicals = payload.get("Technicals") or {}
    shares = payload.get("SharesStats") or {}
    signals: dict[str, Any] = {key: None for key in SIGNAL_KEYS}
    signals["as_of"] = _parse_date(general.get("UpdatedAt")) or fallback_as_of

    trend = earnings.get("Trend")
    current_year = _latest_trend_row(trend, TREND_PERIOD, signals["as_of"])
    next_year = _latest_trend_row(trend, NEXT_TREND_PERIOD, signals["as_of"])
    if next_year is not None and current_year is not None and next_year[0] > current_year[0]:
        _, row = next_year
        next_estimate = _eps(row.get("epsTrendCurrent")) or _eps(row.get("earningsEstimateAvg"))
        signals.update(
            eps_next_year_revision_30d=_relative_change(next_estimate, _eps(row.get("epsTrend30daysAgo"))),
            eps_next_year_revision_90d=_relative_change(next_estimate, _eps(row.get("epsTrend90daysAgo"))),
        )
    if current_year is not None:
        period_end, row = current_year
        estimate = _eps(row.get("epsTrendCurrent")) or _eps(row.get("earningsEstimateAvg"))
        estimate_30d = _eps(row.get("epsTrend30daysAgo"))
        estimate_90d = _eps(row.get("epsTrend90daysAgo"))
        up = _count(row.get("epsRevisionsUpLast30days"))
        down = _count(row.get("epsRevisionsDownLast30days"))
        analysts = _count(row.get("earningsEstimateNumberOfAnalysts"))
        breadth = None
        if up is not None and down is not None and analysts:
            breadth = (up - down) / analysts
            if abs(breadth) > 1:
                breadth = None
        signals.update(
            eps_period_end=period_end,
            eps_estimate=estimate,
            eps_estimate_30d_ago=estimate_30d,
            eps_estimate_90d_ago=estimate_90d,
            eps_revision_30d=_relative_change(estimate, estimate_30d),
            eps_revision_90d=_relative_change(estimate, estimate_90d),
            eps_revisions_up_30d=up,
            eps_revisions_down_30d=down,
            eps_estimate_analysts=analysts or None,
            eps_revision_breadth_30d=breadth,
        )

    shares_short = _positive(technicals.get("SharesShort")) or _positive(shares.get("SharesShort"))
    prior_short = (
        _positive(technicals.get("SharesShortPriorMonth"))
        or _positive(shares.get("SharesShortPriorMonth"))
    )
    short_float = _number(shares.get("ShortPercentFloat"))
    if short_float is None:
        short_float = _number(technicals.get("ShortPercent"))
    short_ratio = _number(technicals.get("ShortRatio"))
    signals.update(
        shares_short=int(shares_short) if shares_short is not None else None,
        shares_short_prior_month=int(prior_short) if prior_short is not None else None,
        short_interest_change=(
            shares_short / prior_short - 1
            if shares_short is not None and prior_short is not None
            else None
        ),
        short_float=short_float if short_float is not None and short_float >= 0 else None,
        short_ratio=short_ratio if short_ratio is not None and short_ratio >= 0 else None,
    )
    return signals


def screener_values(payload: Any) -> dict[str, Any]:
    signals = extract_expectation_signals(payload)
    return {key: signals[key] for key in SCREENER_FIELDS}


def _agreed_change(current_year: Optional[float], next_year: Optional[float], direction: str) -> Optional[float]:
    """The smaller move of the two fiscal years, or the current year's alone."""
    if current_year is None or next_year is None:
        return current_year
    return max(current_year, next_year) if direction == "cut" else min(current_year, next_year)


def estimate_revision_signal(signals: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Direction and severity of consensus EPS revisions.

    The 30-day change decides the direction when it is material, then the
    30-day revision breadth, then the 90-day change, so a fresh reversal is
    not drowned out by an older trend. A percentage change counts only when
    the next fiscal year moves the same way by at least as much as the
    threshold, which screens out one-off items. Only cuts carry a severity.
    """
    analysts = signals.get("eps_estimate_analysts")
    if not analysts or analysts < MIN_REVISION_ANALYSTS:
        return None
    changes = {
        (window, direction): _agreed_change(
            signals.get(f"eps_revision_{window}"),
            signals.get(f"eps_next_year_revision_{window}"),
            direction,
        )
        for window in ("30d", "90d")
        for direction in ("cut", "raise")
    }
    breadth = signals.get("eps_revision_breadth_30d")
    up = signals.get("eps_revisions_up_30d") or 0
    down = signals.get("eps_revisions_down_30d") or 0

    def crossed(window: str, direction: str) -> bool:
        change = changes[(window, direction)]
        if change is None:
            return False
        if direction == "cut":
            return change <= (ESTIMATE_CUT_30D if window == "30d" else ESTIMATE_CUT_90D)[0]
        return change >= (ESTIMATE_RAISE_30D if window == "30d" else ESTIMATE_RAISE_90D)

    direction = basis = None
    if crossed("30d", "cut"):
        direction, basis = "cut", "30d"
    elif crossed("30d", "raise"):
        direction, basis = "raise", "30d"
    elif breadth is not None and breadth <= -REVISION_BREADTH and down >= MIN_DIRECTIONAL_REVISIONS:
        direction, basis = "cut", "breadth"
    elif breadth is not None and breadth >= REVISION_BREADTH and up >= MIN_DIRECTIONAL_REVISIONS:
        direction, basis = "raise", "breadth"
    elif crossed("90d", "cut"):
        direction, basis = "cut", "90d"
    elif crossed("90d", "raise"):
        direction, basis = "raise", "90d"
    if direction is None:
        return None

    severity = None
    if direction == "cut":
        cut_30d, cut_90d = changes[("30d", "cut")], changes[("90d", "cut")]
        high = (
            (cut_30d is not None and cut_30d <= ESTIMATE_CUT_30D[1])
            or (cut_90d is not None and cut_90d <= ESTIMATE_CUT_90D[1])
        )
        severity = "high" if high else "warning"
    return {"direction": direction, "basis": basis, "severity": severity}


def short_interest_signal(signals: dict[str, Any]) -> Optional[dict[str, Any]]:
    short_float = signals.get("short_float")
    if short_float is None:
        return None
    change = signals.get("short_interest_change")
    rising = None
    if change is not None and change >= SHORT_INTEREST_RISE[0] and short_float >= SHORT_FLOAT_FOR_RISE[0]:
        high = change >= SHORT_INTEREST_RISE[1] and short_float >= SHORT_FLOAT_FOR_RISE[1]
        rising = "high" if high else "warning"
    crowded = None
    if short_float >= SHORT_FLOAT_LEVEL[0]:
        crowded = "high" if short_float >= SHORT_FLOAT_LEVEL[1] else "warning"
    if rising is None and crowded is None:
        return None
    severity = "high" if "high" in (rising, crowded) else "warning"
    return {"kind": "rising" if rising else "crowded", "severity": severity}


def signal_age_days(signals: dict[str, Any], reference_date: date) -> Optional[int]:
    as_of = signals.get("as_of")
    return (reference_date - as_of).days if isinstance(as_of, date) else None


def _pct(value: float, digits: int = 1) -> str:
    return f"{value * 100:.{digits}f}%"


def _signed_pct(value: float, digits: int = 1) -> str:
    return f"{value * 100:+.{digits}f}%"


WINDOW_LABELS = {"30d": "30 days", "90d": "90 days"}


def _estimate_cut_message(signals: dict[str, Any], basis: str) -> str:
    """Name the levels, not just the percentage, so loss estimates read clearly."""
    up = signals.get("eps_revisions_up_30d") or 0
    down = signals.get("eps_revisions_down_30d") or 0
    analysts = signals.get("eps_estimate_analysts")
    counts = f"{up} up / {down} down revisions among {analysts} analysts in 30 days"
    parts = []
    if basis in WINDOW_LABELS:
        other = "90d" if basis == "30d" else "30d"
        detail = _signed_pct(signals[f"eps_revision_{basis}"])
        if signals.get(f"eps_revision_{other}") is not None:
            detail += f"; {_signed_pct(signals[f'eps_revision_{other}'])} over {WINDOW_LABELS[other]}"
        parts.append(
            f"Current-FY consensus EPS moved from {signals[f'eps_estimate_{basis}_ago']:.2f} to "
            f"{signals['eps_estimate']:.2f} over {WINDOW_LABELS[basis]} ({detail})"
        )
    else:
        parts.append(f"Net {abs(signals['eps_revision_breadth_30d']):.0%} of covering analysts cut the current-FY EPS estimate ({counts})")
    next_year = [
        f"{_signed_pct(signals[f'eps_next_year_revision_{window}'])} over {label}"
        for window, label in WINDOW_LABELS.items()
        if signals.get(f"eps_next_year_revision_{window}") is not None
    ]
    if next_year:
        parts.append(f"next FY {', '.join(next_year)}")
    if basis in WINDOW_LABELS:
        parts.append(counts)
    return "; ".join(parts) + "."


def signal_warnings(signals: dict[str, Any], *, source_date: Optional[str]) -> list[dict[str, Any]]:
    """Key-risk records in the decision-support warning shape."""
    warnings: list[dict[str, Any]] = []
    estimate = estimate_revision_signal(signals)
    if estimate and estimate["direction"] == "cut":
        warnings.append({
            "id": "estimate_cuts",
            "triggered": True,
            "severity": estimate["severity"],
            "title": "Analysts cutting EPS estimates",
            "message": _estimate_cut_message(signals, estimate["basis"]),
            "metric": {"30d": "eps_revision_30d", "90d": "eps_revision_90d"}.get(
                estimate["basis"], "eps_revision_breadth_30d"
            ),
            "current": signals.get("eps_estimate"),
            "previous": (
                signals.get("eps_estimate_90d_ago")
                if estimate["basis"] == "90d"
                else signals.get("eps_estimate_30d_ago")
            ),
            "evidence_metric": "expectations",
            "evidence_id": None,
            "category": "expectations",
            "source_date": source_date,
        })
    short = short_interest_signal(signals)
    if short:
        parts = [f"Short interest is {_pct(signals['short_float'])} of float"]
        if signals.get("short_interest_change") is not None:
            parts.append(f"shares sold short changed {_signed_pct(signals['short_interest_change'], 0)} month over month")
        if signals.get("short_ratio") is not None:
            parts.append(f"{signals['short_ratio']:.1f} days to cover")
        warnings.append({
            "id": "short_interest",
            "triggered": True,
            "severity": short["severity"],
            "title": "Short interest rising" if short["kind"] == "rising" else "Heavily shorted",
            "message": "; ".join(parts) + ".",
            "metric": "short_interest_change" if short["kind"] == "rising" else "short_float",
            "current": signals.get("shares_short"),
            "previous": signals.get("shares_short_prior_month"),
            "evidence_metric": "short_interest",
            "evidence_id": None,
            "category": "positioning",
            "source_date": source_date,
        })
    return warnings


def json_safe(signals: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.isoformat() if isinstance(value, date) else value
        for key, value in signals.items()
    }


async def load_expectation_signals(
    ticker: str,
    db: AsyncSession,
    reference_date: date,
) -> dict[str, Any]:
    """Signals from the ticker's latest stored fundamentals payload.

    Returns ``available=False`` with a note instead of raising, so callers can
    show what is missing.
    """
    snapshot, payload = await load_latest_fundamentals_snapshot(ticker.upper(), db)
    context: dict[str, Any] = {
        "available": False,
        "source": SOURCE_LABEL,
        "as_of": None,
        "max_age_days": MAX_SIGNAL_AGE_DAYS,
        "signals": None,
        "estimate_signal": None,
        "short_signal": None,
        "notes": [],
    }
    if snapshot is None or payload is None:
        context["notes"].append(
            "No fundamentals snapshot is stored for this ticker."
            if snapshot is None
            else "The latest fundamentals snapshot could not be read."
        )
        return context
    signals = extract_expectation_signals(
        payload,
        fallback_as_of=_parse_date(snapshot.fetched_at),
    )
    context["as_of"] = signals["as_of"].isoformat() if signals["as_of"] else None
    context["signals"] = json_safe(signals)
    age = signal_age_days(signals, reference_date)
    if age is None or age > MAX_SIGNAL_AGE_DAYS:
        context["notes"].append(
            f"Consensus and short-interest data are {age} days old; flags need data from the last {MAX_SIGNAL_AGE_DAYS} days."
            if age is not None
            else "The fundamentals snapshot has no update date."
        )
        return context
    context["available"] = True
    context["estimate_signal"] = estimate_revision_signal(signals)
    context["short_signal"] = short_interest_signal(signals)
    if signals["eps_period_end"] is None:
        context["notes"].append("The provider publishes no current fiscal-year EPS consensus.")
    elif not signals["eps_estimate_analysts"] or signals["eps_estimate_analysts"] < MIN_REVISION_ANALYSTS:
        context["notes"].append(
            f"Fewer than {MIN_REVISION_ANALYSTS} analysts cover the current fiscal year, so revisions are not assessed."
        )
    if signals["short_float"] is None:
        context["notes"].append("Short interest as a share of float is unavailable.")
    return context
