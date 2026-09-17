"""Month-end index-level valuation aggregates over point-in-time membership.

Each month-end point aggregates the reconstructed per-member multiples from
``services.valuation_history`` over the members that the index actually
contained on that date. Multi-class members are grouped into one company:
equity is summed across classes while company-wide earnings are counted once.
Months below the coverage gate stay gaps with the reason recorded in the run's
quality report; nothing is forward-filled, averaged or zero-filled.
"""

from __future__ import annotations

import asyncio
import calendar
import gzip
import json
import logging
from datetime import date, timedelta
from statistics import median
from typing import Any, Optional

from sqlalchemy import delete, desc, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.trading_calendar import is_us_market_session, latest_completed_us_session
from database import async_session_maker
from models import DataPublication, IndexValuationSnapshot, PipelineRun, UniverseMembership
from services.pipeline_runs import (
    begin_pipeline_run,
    finish_pipeline_run,
    latest_published_date,
    publish_datasets_and_finish,
    update_pipeline_run,
)
from services.universe import HISTORICAL_UNIVERSE_DATASET, HISTORICAL_UNIVERSE_SOURCE
from services.valuation_history import get_valuation_history


logger = logging.getLogger(__name__)

INDEX_VALUATION_DATASET = "index_valuation"
INDEX_VALUATION_UNIVERSES = ("SP500",)
INDEX_VALUATION_RETENTION_RUNS = 5
# Known multi-class S&P 500 members (verified against the provider's GSPC
# membership history) whose SEC company-ticker entries may be missing after
# delisting. The SEC file remains the primary grouping source.
STATIC_COMPANY_GROUPS = (
    ("GOOG", "GOOGL"),
    ("FOXA", "FOX"),
    ("NWSA", "NWS"),
    ("CMCSA", "CMCSK"),
    ("TFCFA", "TFCF"),
    ("UAA", "UA"),
    ("LBRDA", "LBRDK"),
    ("MOB.A", "MOB.B"),
)
METHODOLOGY = [
    "Reconstructed history, not a point-in-time backtest dataset: initial provider payloads may contain later restatements. Recorded revisions become effective only after their availability date.",
    "Membership is point-in-time: every month uses the index constituents whose provider membership interval covers that month's final trading session (never a holiday calendar month-end), and the underlying price session must also fall inside the interval. Only completed months are published, and prices are capped at the publication's target session.",
    "Multi-class members are grouped into one company: the documented static pairs take precedence, with the SEC CIK map (cached official company-tickers file) grouping every further pair, because the current SEC file can list only one class of a delisted multi-class company. The provider reports company-wide statement shares on every class, so company equity uses the primary (largest) class's equity proxy instead of summing classes; company-wide earnings are counted exactly once and must agree across classes.",
    "Member earnings follow the per-stock P/E gates: currency match, four consecutive disclosed quarters, a latest statement no older than 180 days, annual reconciliation and earnings-quality quarantines. A non-positive TTM total stays a valid negative contribution instead of becoming a gap.",
    "index_pe is the aggregate sum(company equity) / sum(TTM earnings) including loss-makers; index_pe_earners repeats the ratio over profitable companies only; median_pe is the median of company-level P/E ratios.",
    "Provider statement shares are split-adjusted weighted-average proxies, so equity totals are estimates of market capitalization, not verified historical market caps.",
    "Months with insufficient member or input coverage remain gaps with the reason stored on the month and aggregated in the quality report; gaps are never interpolated and failed runs publish nothing.",
]


class IndexValuationUnavailable(RuntimeError):
    pass


class IndexValuationUniverseUnavailable(ValueError):
    pass


def _base_code(ticker: str) -> str:
    return ticker.split(".", 1)[0] if "." in ticker else ticker


def load_cik_map(path: str) -> dict[str, str]:
    """Best-effort ticker -> CIK map from the cached SEC company-tickers file.

    A missing or unreadable file is not fatal: static groups and the
    per-ticker fallback still apply, and the run records a warning.
    """
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, EOFError, ValueError, TypeError) as exc:
        logger.warning("SEC company-tickers cache unavailable at %s: %s", path, exc)
        return {}
    entries = payload.values() if isinstance(payload, dict) else payload
    mapping: dict[str, str] = {}
    for entry in entries:
        if isinstance(entry, dict) and entry.get("ticker") and entry.get("cik_str"):
            mapping[str(entry["ticker"]).upper()] = str(entry["cik_str"])
    return mapping


def company_keys(tickers: list[str], cik_map: dict[str, str]) -> dict[str, str]:
    """Resolve every member ticker to one company identity.

    The verified static pairs take precedence over the current SEC file: the
    file can list only one class of a delisted multi-class company (CMCSA is
    listed while CMCSK is not), which would otherwise split a declared pair
    into two companies. The SEC CIK map still groups every multi-class pair
    the static list does not know about.
    """
    static: dict[str, str] = {}
    for group in STATIC_COMPANY_GROUPS:
        canonical = group[0]
        for member in group:
            static[member.upper()] = f"STATIC:{canonical}"
    resolved: dict[str, str] = {}
    for ticker in tickers:
        base = _base_code(ticker).upper()
        if base in static:
            resolved[ticker] = static[base]
        elif base in cik_map:
            resolved[ticker] = f"CIK:{cik_map[base]}"
        else:
            resolved[ticker] = f"TICKER:{base}"
    return resolved


def last_session_of_month(year: int, month: int) -> Optional[date]:
    """The final US session of a calendar month, or None when there is none."""
    day = calendar.monthrange(year, month)[1]
    cursor = date(year, month, day)
    for _ in range(7):
        if is_us_market_session(cursor):
            return cursor
        cursor -= timedelta(days=1)
    return None


def completed_month_end_labels(start: date, target: date) -> list[date]:
    """Calendar month-end labels for every month fully traded by ``target``.

    A month is complete when its final US session closed at or before the
    target; the running month therefore never appears with a future label,
    and a month whose final session was observed is included even when that
    session precedes the calendar month-end (e.g. the 28th before a holiday
    Monday).
    """
    labels: list[date] = []
    year, month = start.year, start.month
    while (year, month) <= (target.year, target.month):
        last_session = last_session_of_month(year, month)
        if last_session is not None and last_session <= target:
            labels.append(date(year, month, calendar.monthrange(year, month)[1]))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return labels


def _interval_covers(interval: dict, day: date) -> bool:
    if interval["effective_from"] > day:
        return False
    effective_to = interval.get("effective_to")
    return effective_to is None or effective_to >= day


def build_index_valuation_rows(
    per_ticker_points: dict[str, dict[str, dict]],
    memberships: list[dict],
    sample_dates: list[date],
    *,
    min_members: int,
    min_coverage: float,
    cik_map: Optional[dict[str, str]] = None,
) -> list[dict]:
    """Aggregate one row per sample month. Pure; safe to run in a worker thread.

    ``per_ticker_points`` maps ticker -> month label ISO -> reconstructed point
    (``equity``, ``earnings_ttm``, ``earnings_reason``, ``price_date``).
    """
    keys = company_keys(
        sorted({interval["ticker"] for interval in memberships}),
        cik_map if cik_map is not None else load_cik_map(
            settings.INDEX_VALUATION_SEC_TICKERS_PATH
        ),
    )
    rows: list[dict] = []
    for day in sample_dates:
        # Membership is evaluated at the month's final trading session, not
        # the calendar month-end: a holiday month-end (e.g. Memorial Day)
        # would wrongly drop members whose exit is the last session and could
        # wrongly include joins effective only after that session.
        as_of = last_session_of_month(day.year, day.month) or day
        members = [iv for iv in memberships if _interval_covers(iv, as_of)]
        companies: dict[str, list[dict]] = {}
        for interval in members:
            ticker = interval["ticker"]
            point = per_ticker_points.get(ticker, {}).get(day.isoformat())
            if point is None:
                continue
            price_date = date.fromisoformat(point["price_date"])
            # The observation session itself must belong to the membership
            # interval; a month-end label is not enough for mid-month joins.
            if price_date < interval["effective_from"]:
                continue
            effective_to = interval.get("effective_to")
            if effective_to is not None and price_date > effective_to:
                continue
            if point["equity"] is None or point["earnings_ttm"] is None:
                continue
            companies.setdefault(keys[ticker], []).append({
                "ticker": ticker,
                "equity": point["equity"],
                "earnings_ttm": point["earnings_ttm"],
            })
        member_count = len({keys[interval["ticker"]] for interval in members})
        covered: dict[str, dict] = {}
        multi_class_conflicts = 0
        for company, class_rows in companies.items():
            primary = max(class_rows, key=lambda row: (row["equity"], row["ticker"]))
            conflict = any(
                other["earnings_ttm"] != primary["earnings_ttm"]
                and abs(other["earnings_ttm"] - primary["earnings_ttm"])
                > max(1.0, 0.02 * max(abs(other["earnings_ttm"]), abs(primary["earnings_ttm"])))
                for other in class_rows
                if other is not primary
            )
            if conflict:
                # The classes carry company-wide earnings that disagree;
                # which one is right is unknown, so the company stays a gap.
                multi_class_conflicts += 1
                continue
            # The provider reports company-wide statement shares on every
            # class, so each class's equity already approximates the whole
            # company. Summing classes would double-count the numerator while
            # earnings count once; the primary class stands in for the company.
            covered[company] = {
                "equity": primary["equity"],
                "earnings_ttm": primary["earnings_ttm"],
            }
        covered_count = len(covered)
        coverage_pct = 100.0 * covered_count / member_count if member_count else None
        equity_total = sum(row["equity"] for row in covered.values()) or None
        earnings_total = sum(row["earnings_ttm"] for row in covered.values()) if covered else None
        earners = {company: row for company, row in covered.items() if row["earnings_ttm"] > 0}
        earnings_earners = sum(row["earnings_ttm"] for row in earners.values()) if earners else None
        company_pes = [
            row["equity"] / row["earnings_ttm"] for row in earners.values()
        ]
        reason = None
        if member_count < min_members:
            reason = "insufficient-members"
        elif coverage_pct is None or coverage_pct < 100.0 * min_coverage:
            reason = "insufficient-coverage"
        rows.append({
            "universe": "SP500",
            "date": day,
            "member_count": member_count,
            "covered_count": covered_count,
            "loss_maker_count": sum(
                1 for row in covered.values() if row["earnings_ttm"] < 0
            ),
            "coverage_pct": coverage_pct,
            "equity_total": None if reason else equity_total,
            "earnings_ttm_total": None if reason else earnings_total,
            "earnings_ttm_earners": None if reason else earnings_earners,
            "index_pe": (
                None
                if reason or earnings_total is None or earnings_total <= 0
                else equity_total / earnings_total
            ),
            "index_pe_earners": (
                None
                if reason or earnings_earners is None or earnings_earners <= 0
                else sum(row["equity"] for row in earners.values()) / earnings_earners
            ),
            "median_pe": None if reason or len(company_pes) < 2 else median(company_pes),
            "reason": reason,
            "multi_class_conflicts": multi_class_conflicts,
        })
    return rows


def validate_index_valuation_rows(rows: list[dict], min_month_coverage: float) -> dict:
    errors: list[str] = []
    gap_reasons: dict[str, int] = {}
    for row in rows:
        if row["reason"]:
            gap_reasons[row["reason"]] = gap_reasons.get(row["reason"], 0) + 1
    total = len(rows)
    valid = total - sum(gap_reasons.values())
    month_coverage = valid / total if total else 0.0
    if total == 0:
        errors.append("No sample months were produced.")
    elif month_coverage < min_month_coverage:
        errors.append(
            f"Only {valid}/{total} months are valid; {min_month_coverage:.0%} are required."
        )
    conflicts = sum(row["multi_class_conflicts"] for row in rows)
    return {
        "passed": not errors,
        "metrics": {
            "months_total": total,
            "months_valid": valid,
            "month_coverage": month_coverage,
            "minimum_month_coverage": min_month_coverage,
            "gap_reasons": gap_reasons,
            "multi_class_conflicts": conflicts,
            "first_date": rows[0]["date"].isoformat() if rows else None,
            "last_date": rows[-1]["date"].isoformat() if rows else None,
            "minimum_member_count": min((row["member_count"] for row in rows), default=0),
            "minimum_covered_count": min((row["covered_count"] for row in rows), default=0),
        },
        "errors": errors,
        "warnings": [],
    }


async def _load_member_points(
    tickers: list[str],
    concurrency: int,
    through: date,
) -> tuple[dict[str, dict[str, dict]], list[str]]:
    """Reconstruct month-end points for every ever-member, one session each.

    ``through`` caps every loaded price at the publication's target session so
    a database that carries later sessions can never leak future prices or a
    future split reference into the series.
    """
    semaphore = asyncio.Semaphore(max(1, concurrency))
    per_ticker: dict[str, dict[str, dict]] = {}
    warnings: list[str] = []

    async def load(ticker: str) -> None:
        async with semaphore:
            try:
                async with async_session_maker() as db:
                    history = await get_valuation_history(
                        ticker, db, interval="1mo", through=through
                    )
                per_ticker[ticker] = {
                    point["date"]: point for point in history["points"]
                }
                for warning in history["warnings"]:
                    warnings.append(f"{ticker}: {warning}")
            except Exception as exc:
                warnings.append(f"{ticker}: reconstruction failed: {exc}")
                logger.warning("Index valuation reconstruction failed for %s: %s", ticker, exc)

    await asyncio.gather(*(load(ticker) for ticker in tickers))
    return per_ticker, warnings


async def _published_run_for_date(db: AsyncSession, dataset: str, as_of_date: date) -> Optional[int]:
    result = await db.execute(
        select(DataPublication.pipeline_run_id)
        .join(PipelineRun, PipelineRun.id == DataPublication.pipeline_run_id)
        .where(
            DataPublication.dataset == dataset,
            DataPublication.as_of_date == as_of_date,
            DataPublication.status == "published",
            PipelineRun.status == "published",
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def refresh_index_valuation(target_date: date) -> dict:
    """Recompute and publish the full month-end series for one target session."""
    target = (
        target_date
        if is_us_market_session(target_date)
        else latest_completed_us_session(target_date)
    )
    history_start = settings.INDEX_VALUATION_HISTORY_START
    if history_start > target:
        return {
            "status": "deferred",
            "reason": "history-start-after-target",
            "as_of_date": target.isoformat(),
        }

    async with async_session_maker() as dependency_db:
        missing = [
            dataset
            for dataset in ("price_history", HISTORICAL_UNIVERSE_DATASET)
            if await _published_run_for_date(dependency_db, dataset, target) is None
        ]
    if missing:
        return {
            "status": "deferred",
            "reason": "missing-publications",
            "missing": missing,
            "as_of_date": target.isoformat(),
        }

    async with async_session_maker() as existing_db:
        existing_run = await _published_run_for_date(
            existing_db, INDEX_VALUATION_DATASET, target
        )
        if existing_run is not None:
            count = await existing_db.scalar(
                select(IndexValuationSnapshot.id).where(
                    IndexValuationSnapshot.pipeline_run_id == existing_run
                ).limit(1)
            )
            if count is not None:
                return {
                    "status": "skipped",
                    "reason": "already-published",
                    "as_of_date": target.isoformat(),
                }

    async with async_session_maker() as source_db:
        membership_result = await source_db.execute(
            select(UniverseMembership).where(
                UniverseMembership.universe.in_(INDEX_VALUATION_UNIVERSES),
                UniverseMembership.source == HISTORICAL_UNIVERSE_SOURCE,
                UniverseMembership.effective_from <= target,
                (
                    UniverseMembership.effective_to.is_(None)
                    | (UniverseMembership.effective_to >= history_start)
                ),
            )
        )
        memberships = [
            {
                "ticker": membership.ticker,
                "effective_from": membership.effective_from,
                "effective_to": membership.effective_to,
            }
            for membership in membership_result.scalars()
        ]
    if not memberships:
        return {
            "status": "deferred",
            "reason": "no-membership-history",
            "as_of_date": target.isoformat(),
        }

    ever_members = sorted({interval["ticker"] for interval in memberships})
    run_id = await begin_pipeline_run("index_valuation", target)
    try:
        await update_pipeline_run(run_id, "reconstructing_members", len(ever_members))
        per_ticker, load_warnings = await _load_member_points(
            ever_members, settings.INDEX_VALUATION_COMPUTE_CONCURRENCY, through=target
        )
        await update_pipeline_run(run_id, "aggregating_months")
        sample_dates = completed_month_end_labels(history_start, target)
        cik_map = await asyncio.to_thread(
            load_cik_map, settings.INDEX_VALUATION_SEC_TICKERS_PATH
        )
        rows = await asyncio.to_thread(
            build_index_valuation_rows,
            per_ticker,
            memberships,
            sample_dates,
            min_members=settings.PIPELINE_MIN_SP500_SIZE,
            min_coverage=settings.PIPELINE_MIN_INDEX_VALUATION_COVERAGE,
            cik_map=cik_map,
        )
        quality = validate_index_valuation_rows(
            rows, settings.INDEX_VALUATION_MIN_MONTH_COVERAGE
        )
        quality["metrics"]["cik_map_available"] = bool(cik_map)
        quality["warnings"] = load_warnings[:200]
        if not cik_map:
            quality["warnings"].append(
                "SEC company-tickers cache is unavailable; multi-class grouping "
                "falls back to the documented static list. Run "
                "scripts/backfill_index_valuation.py to download it."
            )
        if not quality["passed"]:
            raise ValueError(
                "Index valuation quality gate failed: " + "; ".join(quality["errors"])
            )
        snapshot_rows = [
            {key: row[key] for key in (
                "universe", "date", "member_count", "covered_count",
                "loss_maker_count", "coverage_pct", "equity_total",
                "earnings_ttm_total", "earnings_ttm_earners",
                "index_pe", "index_pe_earners", "median_pe", "reason",
            )}
            for row in rows
        ]
        for row, snapshot in zip(rows, snapshot_rows):
            snapshot["pipeline_run_id"] = run_id
        await update_pipeline_run(run_id, "publishing_index_valuation", len(snapshot_rows))
        async with async_session_maker() as db, db.begin():
            for start in range(0, len(snapshot_rows), 500):
                await db.execute(
                    insert(IndexValuationSnapshot).values(
                        snapshot_rows[start:start + 500]
                    )
                )
            await publish_datasets_and_finish(
                db,
                [INDEX_VALUATION_DATASET],
                target,
                run_id,
                quality_report=quality,
                records_processed=len(snapshot_rows),
            )
            expired_result = await db.execute(
                select(DataPublication)
                .where(DataPublication.dataset == INDEX_VALUATION_DATASET)
                .order_by(desc(DataPublication.as_of_date))
                .offset(INDEX_VALUATION_RETENTION_RUNS)
            )
            expired = list(expired_result.scalars())
            expired_run_ids = [publication.pipeline_run_id for publication in expired]
            if expired_run_ids:
                await db.execute(
                    delete(IndexValuationSnapshot).where(
                        IndexValuationSnapshot.pipeline_run_id.in_(expired_run_ids)
                    )
                )
                for publication in expired:
                    await db.delete(publication)
            retained_run_ids = list((await db.execute(
                select(DataPublication.pipeline_run_id).where(
                    DataPublication.dataset == INDEX_VALUATION_DATASET
                )
            )).scalars())
            if retained_run_ids:
                await db.execute(
                    delete(IndexValuationSnapshot).where(
                        IndexValuationSnapshot.pipeline_run_id.not_in(retained_run_ids)
                    )
                )
        return {
            "run_id": run_id,
            "status": "published",
            "as_of_date": target.isoformat(),
            "ever_members": len(ever_members),
            **quality["metrics"],
        }
    except asyncio.CancelledError:
        await finish_pipeline_run(run_id, "cancelled")
        raise
    except Exception as exc:
        await finish_pipeline_run(
            run_id,
            "failed",
            quality_report=quality if "quality" in locals() else None,
            error_message=str(exc),
        )
        raise


async def get_index_valuation(db: AsyncSession, universe: str) -> dict:
    """Serve the newest published month-end series for one universe."""
    if universe not in INDEX_VALUATION_UNIVERSES:
        raise IndexValuationUniverseUnavailable(
            "Index valuation is only available for " + ", ".join(INDEX_VALUATION_UNIVERSES)
        )
    publication_result = await db.execute(
        select(DataPublication)
        .join(PipelineRun, PipelineRun.id == DataPublication.pipeline_run_id)
        .where(
            DataPublication.dataset == INDEX_VALUATION_DATASET,
            DataPublication.status == "published",
            PipelineRun.status == "published",
        )
        .order_by(desc(DataPublication.as_of_date))
        .limit(1)
    )
    publication = publication_result.scalars().first()
    if publication is None:
        raise IndexValuationUnavailable("Index valuation has not been published yet.")
    snapshot_rows = list((await db.execute(
        select(IndexValuationSnapshot)
        .where(IndexValuationSnapshot.pipeline_run_id == publication.pipeline_run_id)
        .where(IndexValuationSnapshot.universe == universe)
        .order_by(IndexValuationSnapshot.date)
    )).scalars())
    if not snapshot_rows:
        raise IndexValuationUnavailable("Index valuation snapshots are unavailable.")
    run = await db.get(PipelineRun, publication.pipeline_run_id)
    quality = (run.quality_report if run else None) or {}
    series = [row for row in snapshot_rows if row.index_pe is not None]
    latest = series[-1] if series else None
    values = [row.index_pe for row in series]
    expected = latest_completed_us_session(date.today())
    return {
        "meta": {
            "universe": universe,
            "as_of_date": publication.as_of_date.isoformat(),
            "expected_as_of_date": expected.isoformat(),
            "published_at": publication.published_at.isoformat()
            if publication.published_at
            else None,
            "stale": publication.as_of_date < expected,
            "history_start": snapshot_rows[0].date.isoformat(),
            "history_end": snapshot_rows[-1].date.isoformat(),
            "membership_mode": "point_in_time",
            "history_basis": "reconstructed_estimates",
            "price_basis": "split_only",
            "warnings": quality.get("warnings") or [],
        },
        "methodology": METHODOLOGY,
        "points": [
            {
                "date": row.date.isoformat(),
                "index_pe": row.index_pe,
                "index_pe_earners": row.index_pe_earners,
                "median_pe": row.median_pe,
                "member_count": row.member_count,
                "covered_count": row.covered_count,
                "loss_maker_count": row.loss_maker_count,
                "coverage_pct": row.coverage_pct,
                "reason": row.reason,
            }
            for row in snapshot_rows
        ],
        "stats": {
            "months_total": len(snapshot_rows),
            "months_valid": len(series),
            "latest_date": latest.date.isoformat() if latest else None,
            "latest_index_pe": latest.index_pe if latest else None,
            "latest_index_pe_earners": latest.index_pe_earners if latest else None,
            "latest_median_pe": latest.median_pe if latest else None,
            "median_index_pe": median(values) if values else None,
            "min_index_pe": min(values) if values else None,
            "max_index_pe": max(values) if values else None,
            "average_coverage_pct": (
                sum(row.coverage_pct or 0.0 for row in snapshot_rows) / len(snapshot_rows)
            ),
        },
    }
