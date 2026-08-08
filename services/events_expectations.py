"""Read-only event and forward-consensus evidence for a single security."""

import asyncio
import gzip
import json
import logging
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.time_utils import utc_now
from models import RawDataSnapshot

logger = logging.getLogger(__name__)

_PERIOD_LABELS = {
    "0q": ("Current quarter", 0),
    "+1q": ("Next quarter", 1),
    "0y": ("Current fiscal year", 2),
    "+1y": ("Next fiscal year", 3),
}
_SNAPSHOT_STALE_AFTER_DAYS = 7


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return parsed if parsed is not None and math.isfinite(parsed) else None


def _safe_int(value: Any) -> Optional[int]:
    parsed = _safe_float(value)
    return int(parsed) if parsed is not None else None


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


def _read_json(path: str) -> dict[str, Any]:
    file_path = Path(path)
    opener = gzip.open if file_path.suffix == ".gz" else open
    with opener(file_path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def _snapshot_staleness_note(
    snapshot: RawDataSnapshot,
    reference_date: date,
) -> Optional[str]:
    fetched_at = snapshot.fetched_at
    if fetched_at is None:
        return None
    fetched_date = fetched_at.date() if isinstance(fetched_at, datetime) else _parse_date(fetched_at)
    if fetched_date is None:
        return None
    age_days = (reference_date - fetched_date).days
    if age_days <= _SNAPSHOT_STALE_AFTER_DAYS:
        return None
    return (
        f"Provider snapshot is {age_days} days old; verify event and consensus data "
        "before relying on it."
    )


async def _latest_fundamentals_snapshot(
    ticker: str,
    db: AsyncSession,
) -> tuple[Optional[RawDataSnapshot], Optional[dict[str, Any]]]:
    result = await db.execute(
        select(RawDataSnapshot)
        .where(
            RawDataSnapshot.source == "EODHD",
            RawDataSnapshot.dataset == "fundamentals",
            RawDataSnapshot.details["ticker"].as_string() == ticker,
        )
        .order_by(RawDataSnapshot.fetched_at.desc())
        .limit(1)
    )
    snapshot = result.scalar_one_or_none()

    # Older databases may have JSON details that cannot be queried through the
    # backend's JSON operator. Keep a bounded fallback for those installations.
    if snapshot is None:
        fallback_result = await db.execute(
            select(RawDataSnapshot)
            .where(
                RawDataSnapshot.source == "EODHD",
                RawDataSnapshot.dataset == "fundamentals",
            )
            .order_by(RawDataSnapshot.fetched_at.desc())
            .limit(200)
        )
        snapshot = next(
            (
                item
                for item in fallback_result.scalars().all()
                if isinstance(item.details, dict)
                and str(item.details.get("ticker", "")).upper() == ticker
            ),
            None,
        )

    if snapshot is None:
        return None, None

    try:
        payload = await asyncio.to_thread(_read_json, snapshot.storage_path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Unable to read fundamentals snapshot for %s: %s", ticker, exc)
        return snapshot, None
    return snapshot, payload


def _history_events(
    history: Any,
    reference_date: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(history, dict):
        return [], []

    upcoming: list[dict[str, Any]] = []
    reported: list[dict[str, Any]] = []
    for key, raw_item in history.items():
        if not isinstance(raw_item, dict):
            continue
        period_end = _parse_date(raw_item.get("date") or key)
        report_date = _parse_date(raw_item.get("reportDate"))
        if period_end is None or report_date is None:
            continue

        eps_actual = _safe_float(raw_item.get("epsActual"))
        if eps_actual is None and report_date < reference_date:
            # A stale provider row without an actual result is not a reliable
            # future event and should not be presented as a completed report.
            continue
        status = "upcoming" if eps_actual is None else "reported"
        event = {
            "id": f"earnings-{period_end.isoformat()}",
            "kind": "earnings",
            "status": status,
            "title": "Upcoming earnings" if status == "upcoming" else "Earnings reported",
            "event_date": report_date,
            "period_end": period_end,
            "payment_date": None,
            "timing": raw_item.get("beforeAfterMarket"),
            "eps_actual": eps_actual,
            "eps_estimate": _safe_float(raw_item.get("epsEstimate")),
            "eps_difference": _safe_float(raw_item.get("epsDifference")),
            "eps_surprise_percent": _safe_float(raw_item.get("surprisePercent")),
        }
        (upcoming if status == "upcoming" else reported).append(event)

    upcoming.sort(key=lambda item: item["event_date"])
    reported.sort(key=lambda item: item["event_date"], reverse=True)
    return upcoming, reported[:8]


def _dividend_event(
    splits_dividends: Any,
    reference_date: date,
) -> Optional[dict[str, Any]]:
    if not isinstance(splits_dividends, dict):
        return None
    ex_date = _parse_date(splits_dividends.get("ExDividendDate"))
    if ex_date is None or ex_date < reference_date:
        return None
    return {
        "id": f"dividend-{ex_date.isoformat()}",
        "kind": "dividend",
        "status": "upcoming",
        "title": "Ex-dividend date",
        "event_date": ex_date,
        "period_end": None,
        "payment_date": _parse_date(splits_dividends.get("DividendDate")),
        "timing": None,
        "eps_actual": None,
        "eps_estimate": None,
        "eps_difference": None,
        "eps_surprise_percent": None,
    }


def _expectations(trend: Any, reference_date: date) -> list[dict[str, Any]]:
    if not isinstance(trend, dict):
        return []

    rows: list[tuple[int, date, dict[str, Any]]] = []
    for key, raw_item in trend.items():
        if not isinstance(raw_item, dict):
            continue
        period_end = _parse_date(raw_item.get("date") or key)
        if period_end is None or period_end < reference_date:
            continue
        period_code = str(raw_item.get("period") or key)
        label, priority = _PERIOD_LABELS.get(
            period_code,
            (f"Fiscal period ending {period_end.isoformat()}", 10),
        )
        eps_average = _safe_float(
            raw_item.get("epsTrendCurrent") or raw_item.get("earningsEstimateAvg")
        )
        revenue_average = _safe_float(raw_item.get("revenueEstimateAvg"))
        if eps_average is None and revenue_average is None:
            continue
        rows.append((priority, period_end, {
            "period": period_code,
            "label": label,
            "period_end": period_end,
            "eps_average": eps_average,
            "eps_low": _safe_float(raw_item.get("earningsEstimateLow")),
            "eps_high": _safe_float(raw_item.get("earningsEstimateHigh")),
            "eps_growth": _safe_float(
                raw_item.get("earningsEstimateGrowth") or raw_item.get("growth")
            ),
            "revenue_average": revenue_average,
            "revenue_low": _safe_float(raw_item.get("revenueEstimateLow")),
            "revenue_high": _safe_float(raw_item.get("revenueEstimateHigh")),
            "revenue_growth": _safe_float(raw_item.get("revenueEstimateGrowth")),
            "eps_analyst_count": _safe_int(raw_item.get("earningsEstimateNumberOfAnalysts")),
            "revenue_analyst_count": _safe_int(raw_item.get("revenueEstimateNumberOfAnalysts")),
            "eps_revisions_up_7d": _safe_int(raw_item.get("epsRevisionsUpLast7days")),
            "eps_revisions_down_7d": _safe_int(raw_item.get("epsRevisionsDownLast7days")),
            "eps_revisions_up_30d": _safe_int(raw_item.get("epsRevisionsUpLast30days")),
            "eps_revisions_down_30d": _safe_int(raw_item.get("epsRevisionsDownLast30days")),
            "eps_trend_current": _safe_float(raw_item.get("epsTrendCurrent")),
            "eps_trend_7d": _safe_float(raw_item.get("epsTrend7daysAgo")),
            "eps_trend_30d": _safe_float(raw_item.get("epsTrend30daysAgo")),
        }))

    rows.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in rows[:6]]


async def get_events_expectations(
    ticker: str,
    db: AsyncSession,
    reference_date: Optional[date] = None,
) -> dict[str, Any]:
    """Return provider-published events and consensus estimates without external work."""
    ticker = ticker.upper()
    today = reference_date or utc_now().date()
    snapshot, payload = await _latest_fundamentals_snapshot(ticker, db)
    if snapshot is None:
        return {
            "ticker": ticker,
            "source": "EODHD",
            "as_of": None,
            "available": False,
            "next_event": None,
            "upcoming_events": [],
            "recent_earnings": [],
            "expectations": [],
            "wall_street_target_price": None,
            "dividend_yield": None,
            "annual_dividend_per_share": None,
            "data_quality_notes": ["No fundamentals snapshot is available yet."],
        }

    if payload is None:
        return {
            "ticker": ticker,
            "source": snapshot.source,
            "as_of": snapshot.fetched_at,
            "available": False,
            "next_event": None,
            "upcoming_events": [],
            "recent_earnings": [],
            "expectations": [],
            "wall_street_target_price": None,
            "dividend_yield": None,
            "annual_dividend_per_share": None,
            "data_quality_notes": ["The fundamentals snapshot could not be read."],
        }

    earnings = payload.get("Earnings") or {}
    upcoming, reported = _history_events(earnings.get("History"), today)
    dividend = _dividend_event(payload.get("SplitsDividends"), today)
    if dividend is not None:
        upcoming.append(dividend)
        upcoming.sort(key=lambda item: item["event_date"])

    expectations = _expectations(earnings.get("Trend"), today)
    highlights = payload.get("Highlights") or {}
    notes: list[str] = []
    staleness_note = _snapshot_staleness_note(snapshot, today)
    if staleness_note:
        notes.append(staleness_note)
    if not upcoming:
        notes.append("No upcoming event date was published by the provider.")
    if not expectations:
        notes.append("No forward consensus estimates were published by the provider.")

    return {
        "ticker": ticker,
        "source": snapshot.source,
        "as_of": snapshot.fetched_at,
        "available": bool(upcoming or reported or expectations),
        "next_event": upcoming[0] if upcoming else None,
        "upcoming_events": upcoming[:6],
        "recent_earnings": reported,
        "expectations": expectations,
        "wall_street_target_price": _safe_float(highlights.get("WallStreetTargetPrice")),
        "dividend_yield": _safe_float(highlights.get("DividendYield")),
        "annual_dividend_per_share": _safe_float(highlights.get("DividendShare")),
        "data_quality_notes": notes,
    }
