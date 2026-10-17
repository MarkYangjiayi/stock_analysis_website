"""Daily theme_trends publication pipeline (docs/themes/IMPLEMENTATION_SPEC.md §3.4).

Shape follows ``market_breadth.refresh_market_breadth``: dependency and skip checks
and every source read happen before a run row exists; scoring runs off the event
loop; a QualityReport gates publication; rows, publications and the sweep of
superseded same-date rows commit in one short write transaction.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional, Sequence

import numpy as np
import pandas as pd
from sqlalchemy import Float, delete, or_, select, type_coerce
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.time_utils import utc_now
from core.trading_calendar import is_us_market_session, latest_completed_us_session
from database import async_session_maker
from models import (
    CompanyTrendDaily,
    DailyPrice,
    DataPublication,
    PipelineRun,
    Theme,
    ThemeDaily,
    ThemeMemberDaily,
)
from services.data_quality import DataQualityError, QualityReport
from services.market_breadth import market_sessions_through
from services.pipeline_runs import (
    begin_pipeline_run,
    finish_pipeline_run,
    publish_dataset,
    publish_datasets_and_finish,
    update_pipeline_run,
)
from services.themes.membership import THEME_MEMBERSHIP_DATASET, load_member_spans, load_proxy_inception
from services.themes.news_ingest import NEWS_ATTENTION_DATASET, load_attention
from services.themes.scoring import SCORING_VERSION, score_themes
from services.themes.types import MemberSpan, ScoringInputs, ScoringResult, ThemeMeta
from services.themes.universe import load_theme_universe

logger = logging.getLogger(__name__)

THEME_TRENDS_DATASET = "theme_trends"
THEME_TRENDS_PIPELINE = "theme_trends"
BENCHMARK_TICKER = "SPY.US"
# Trailing sessions every output date needs behind it (x126, 252-session highs at
# min 126 obs, 60-obs time-series z, 120-session residual beta + 60-session cocorr).
SCORING_SESSIONS = 260
# Attention baselines read UTC days d-103..d (7-day sums over a d-97..d-8 baseline).
ATTENTION_LEAD_DAYS = 110
QUERY_CHUNK = 500
WRITE_CHUNK = 500
EXACT_DEPENDENCIES = ("screener", "price_history")
PRICE_COLUMNS = ("ticker", "date", "adjusted_close", "close", "volume")
# Published sessions before the output window that pin stage history: covers the
# 3-session hold, the 20-session emerging window and the top-5 streak.
PUBLISHED_HISTORY_SESSIONS = 30


# ---------------------------------------------------------------------------
# Publication lookups
# ---------------------------------------------------------------------------


def _session_on_or_before(day: date) -> date:
    return day if is_us_market_session(day) else latest_completed_us_session(day)


async def _published_run_for_date(db: AsyncSession, dataset: str, as_of_date: date) -> Optional[int]:
    """Run id behind an exact-date publication whose run also finished as published."""
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


async def _latest_publication_on_or_before(db: AsyncSession, dataset: str, as_of_date: date) -> Optional[date]:
    result = await db.execute(
        select(DataPublication.as_of_date)
        .join(PipelineRun, PipelineRun.id == DataPublication.pipeline_run_id)
        .where(
            DataPublication.dataset == dataset,
            DataPublication.as_of_date <= as_of_date,
            DataPublication.status == "published",
            PipelineRun.status == "published",
        )
        .order_by(DataPublication.as_of_date.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _published_dates_between(db: AsyncSession, dataset: str, start: date, end: date) -> set[date]:
    result = await db.execute(
        select(DataPublication.as_of_date)
        .join(PipelineRun, PipelineRun.id == DataPublication.pipeline_run_id)
        .where(
            DataPublication.dataset == dataset,
            DataPublication.as_of_date >= start,
            DataPublication.as_of_date <= end,
            DataPublication.status == "published",
            PipelineRun.status == "published",
        )
    )
    return set(result.scalars())


async def _published_theme_history(
    db: AsyncSession, sessions: Sequence[date], slugs_by_id: dict[int, str]
) -> list[dict[str, Any]]:
    """Each given session's published theme rows (from that date's own publication).

    ``score_themes(published=...)`` uses them so stage_prev/stage_changed, the stage
    hold, emerging_first_20, rank_change and streak_top5 continue what was served
    instead of being recomputed from this run's shifted window. Only ``sessions``
    are returned: the query reads their min..max range, which can hold output dates
    this run republishes and so must not pin.
    """
    if not sessions:
        return []
    wanted = set(sessions)
    result = await db.execute(
        select(ThemeDaily.theme_id, ThemeDaily.date, ThemeDaily.stage, ThemeDaily.candidate_stage,
               ThemeDaily.rank_now, ThemeDaily.streak_top5)
        .join(DataPublication, DataPublication.pipeline_run_id == ThemeDaily.source_run_id)
        .join(PipelineRun, PipelineRun.id == DataPublication.pipeline_run_id)
        .where(
            DataPublication.dataset == THEME_TRENDS_DATASET,
            DataPublication.status == "published",
            PipelineRun.status == "published",
            DataPublication.as_of_date == ThemeDaily.date,
            ThemeDaily.date >= min(sessions),
            ThemeDaily.date <= max(sessions),
        )
    )
    return [
        {"theme_slug": slugs_by_id[theme_id], "date": day, "stage": stage, "candidate_stage": candidate,
         "rank_now": rank_now, "streak_top5": streak}
        for theme_id, day, stage, candidate, rank_now, streak in result.all()
        if theme_id in slugs_by_id and day in wanted
    ]


def published_date_facts(quality_report: Optional[dict], as_of: date) -> Optional[dict[str, Any]]:
    """Quality facts a ``theme_trends`` run recorded for one of its published dates.

    ``metrics.dates[<iso>]`` holds ``rankable_themes``, ``attention_degraded``,
    ``coverage_pct``, ``warnings`` (and the counts behind them) for every date the
    run published; a backfill publishes many dates under one run, while the
    top-level metrics describe the run's own target only. Reports without a
    per-date entry fall back to the top-level metrics for the target, else None.
    """
    metrics = (quality_report or {}).get("metrics") or {}
    entry = (metrics.get("dates") or {}).get(as_of.isoformat())
    if isinstance(entry, dict):
        return entry
    if metrics.get("as_of_date") in (None, as_of.isoformat()):
        return metrics
    return None


async def _attention_rerun_due(db: AsyncSession, run_id: int, target: date) -> bool:
    """One re-run when the published run lacked news that has since been published.

    The published run must have been attention-degraded on ``target`` *and*
    computed before ``news_attention`` existed for it (judged by the run's facts
    for that date, not its own target's); the re-run records
    ``news_attention_published=True``, so it never repeats.
    """
    run = await db.get(PipelineRun, run_id)
    facts = published_date_facts(run.quality_report if run is not None else None, target)
    if not facts or not facts.get("attention_degraded") or facts.get("news_attention_published"):
        return False
    return await _published_run_for_date(db, NEWS_ATTENTION_DATASET, target) is not None


async def theme_trends_rerun_due(target: date) -> bool:
    """Whether the published ``theme_trends`` run for ``target`` is due its single attention re-run.

    The scheduler's skip-if-published guard consults this so the 07:50 slot can
    replace a 04:50 publication that ran before the news window completed.
    """
    session = _session_on_or_before(target)
    async with async_session_maker() as db:
        run_id = await _published_run_for_date(db, THEME_TRENDS_DATASET, session)
        if run_id is None:
            return False
        return await _attention_rerun_due(db, run_id, session)


# ---------------------------------------------------------------------------
# Source loading
# ---------------------------------------------------------------------------


async def _load_price_rows(db: AsyncSession, tickers: Sequence[str], start: date, end: date) -> list[tuple]:
    """(ticker, date, adjusted_close, close, volume) rows as floats, in 500-ticker chunks.

    ``type_coerce(..., Float)`` skips the Decimal conversion of ``Numeric`` columns,
    which dominates the load time for ~3,000 tickers x 260+ sessions.
    """
    rows: list[tuple] = []
    for start_index in range(0, len(tickers), QUERY_CHUNK):
        chunk = list(tickers[start_index:start_index + QUERY_CHUNK])
        result = await db.execute(
            select(
                DailyPrice.ticker,
                DailyPrice.date,
                type_coerce(DailyPrice.adjusted_close, Float),
                type_coerce(DailyPrice.close, Float),
                type_coerce(DailyPrice.volume, Float),
            ).where(
                DailyPrice.ticker.in_(chunk),
                DailyPrice.date >= start,
                DailyPrice.date <= end,
            )
        )
        rows.extend(tuple(row) for row in result.all())
    return rows


def build_price_frames(
    rows: Iterable[tuple],
    sessions: Sequence[date],
    tickers: Sequence[str],
    *,
    benchmark: str = BENCHMARK_TICKER,
    proxies: Sequence[str] = (),
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.DataFrame]:
    """Wide (sessions x tickers) close/volume frames, the benchmark series and proxy closes.

    Close is ``effective_close``: adjusted close when present, raw close otherwise,
    and NaN unless finite and positive. Missing sessions and tickers stay NaN.
    """
    index = pd.Index(list(sessions), dtype=object)
    ticker_columns = pd.Index(list(tickers), dtype=object)
    proxy_columns = pd.Index(list(proxies), dtype=object)
    frame = pd.DataFrame.from_records(list(rows), columns=list(PRICE_COLUMNS))
    if frame.empty:
        return (
            pd.DataFrame(np.nan, index=index, columns=ticker_columns),
            pd.DataFrame(np.nan, index=index, columns=ticker_columns),
            pd.Series(np.nan, index=index, dtype=float),
            pd.DataFrame(np.nan, index=index, columns=proxy_columns),
        )
    adjusted = pd.to_numeric(frame["adjusted_close"], errors="coerce")
    raw = pd.to_numeric(frame["close"], errors="coerce")
    price = adjusted.where(frame["adjusted_close"].notna(), raw)
    price = price.where(np.isfinite(price) & (price > 0))
    volume = pd.to_numeric(frame["volume"], errors="coerce")
    volume = volume.where(np.isfinite(volume) & (volume >= 0))
    frame = frame.assign(price=price, volume=volume).drop_duplicates(["ticker", "date"], keep="last")
    wide_price = frame.pivot(index="date", columns="ticker", values="price")
    wide_volume = frame.pivot(index="date", columns="ticker", values="volume")

    def aligned(wide: pd.DataFrame, columns: pd.Index) -> pd.DataFrame:
        out = wide.reindex(index=index, columns=columns).astype(float)
        out.index.name = None
        out.columns.name = None
        return out

    close = aligned(wide_price, ticker_columns)
    volume_frame = aligned(wide_volume, ticker_columns)
    proxy_close = aligned(wide_price, proxy_columns)
    if benchmark in wide_price.columns:
        benchmark_series = wide_price[benchmark].reindex(index).astype(float)
    else:
        benchmark_series = pd.Series(np.nan, index=index, dtype=float)
    benchmark_series.index.name = None
    benchmark_series.name = benchmark
    return close, volume_frame, benchmark_series, proxy_close


def _theme_metas(themes: Sequence[Theme], inception: dict[str, Optional[date]]) -> list[ThemeMeta]:
    metas = []
    for theme in themes:
        proxies = tuple(str(etf) for etf in (theme.proxy_etfs or []))
        known = [inception[etf] for etf in proxies if inception.get(etf) is not None]
        metas.append(ThemeMeta(slug=theme.slug, proxy_etfs=proxies, proxy_inception=max(known) if known else None))
    return metas


# ---------------------------------------------------------------------------
# Quality gate
# ---------------------------------------------------------------------------


def _json_safe(value: Any) -> Any:
    """Quality reports go into a JSON column: dates as ISO strings, NaN as None."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _attention_warnings(day: date, degraded_themes: int, reasons: Sequence[str], news_published: bool) -> list[str]:
    detail = f" ({', '.join(reasons)})" if reasons else ""
    warnings = [
        f"Attention degraded on {day.isoformat()}{detail}; heat for "
        f"{degraded_themes} themes uses price, breadth and volume only"
    ]
    if not news_published:
        warnings.append(f"news_attention is not published for {day.isoformat()} yet")
    return warnings


def _active_member_tickers(spans_by_slug: dict[str, list[MemberSpan]], day: date, as_of: date) -> set[str]:
    """Members scored on ``day``: the theme's own intervals, or ``as_of``'s when it had
    none yet (scoring's point-in-time fallback, rows ``pit=False``)."""
    active: set[str] = set()
    for spans in spans_by_slug.values():
        reference = day if any(span.valid_from <= day for span in spans) else as_of
        active.update(span.ticker for span in spans if span.active_on(reference))
    return active


def evaluate_theme_trends(
    result: ScoringResult,
    *,
    target: date,
    active_tickers: Iterable[str],
    priced_tickers: Iterable[str],
    benchmark_priced: bool,
    news_published: bool,
    metrics: Optional[dict[str, Any]] = None,
) -> QualityReport:
    """Publication gate (spec §3.4 step 5). Attention problems only ever warn."""
    errors: list[str] = []
    warnings: list[str] = []
    min_rankable = int(settings.PIPELINE_MIN_RANKABLE_THEMES)
    min_coverage = float(settings.PIPELINE_MIN_THEME_PRICE_COVERAGE)

    target_rows = [row for row in result.theme_rows if row.get("date") == target]
    rankable_rows = [row for row in target_rows if row.get("rankable")]
    if len(rankable_rows) < min_rankable:
        errors.append(
            f"{len(rankable_rows)} rankable themes on {target.isoformat()}; at least {min_rankable} required"
        )

    active = set(active_tickers)
    priced = set(priced_tickers) & active
    coverage = len(priced) / len(active) if active else None
    if coverage is None:
        errors.append(f"No active theme members on {target.isoformat()}")
    elif coverage < min_coverage:
        missing = sorted(active - priced)
        errors.append(
            f"Theme member price coverage {coverage:.1%} on {target.isoformat()} is below {min_coverage:.0%}"
            + (f" (missing: {', '.join(missing[:10])})" if missing else "")
        )
    if not benchmark_priced:
        errors.append(f"{BENCHMARK_TICKER} has no price on {target.isoformat()}")

    missing_heat = sorted(
        (row["date"], row["theme_slug"])
        for row in result.theme_rows
        if row.get("rankable") and (row.get("heat") is None or row.get("heat_now") is None)
    )
    if missing_heat:
        errors.append(
            f"{len(missing_heat)} rankable theme rows have no heat: "
            + ", ".join(f"{slug}@{day.isoformat()}" for day, slug in missing_heat[:10])
        )

    rows_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in result.theme_rows:
        if row.get("rankable"):
            rows_by_date[row["date"]].append(row)
    for day in sorted(rows_by_date):
        for field in ("rank_now", "rank_trend"):
            ranks = [row.get(field) for row in rows_by_date[day]]
            present = [rank for rank in ranks if rank is not None]
            duplicates = sorted(rank for rank, count in Counter(present).items() if count > 1)
            if duplicates:
                errors.append(f"Duplicate {field} on {day.isoformat()}: {duplicates[:10]}")
            if len(present) != len(ranks):
                errors.append(f"{len(ranks) - len(present)} rankable themes lack {field} on {day.isoformat()}")

    # Scoring's verdict (incomplete news days, news-volume band, member coverage) is
    # authoritative; the news_attention publication only explains it and decides
    # whether a later re-run can help. Unrankable themes (e.g. one added to the
    # taxonomy before its first membership refresh) have no attention coverage by
    # construction and never flag the run.
    diagnostics = result.diagnostics or {}
    degraded_themes = sorted(row["theme_slug"] for row in rankable_rows if row.get("attention_degraded"))
    attention_degraded = bool(diagnostics.get("attention_degraded")) or bool(degraded_themes)
    if attention_degraded:
        reasons = [str(reason) for reason in diagnostics.get("attention_day_reasons") or []]
        warnings.extend(_attention_warnings(target, len(degraded_themes), reasons, news_published))

    report_metrics = dict(metrics or {})
    report_metrics.update(
        {
            "as_of_date": target.isoformat(),
            "rankable_themes": len(rankable_rows),
            "min_rankable_themes": min_rankable,
            "active_member_tickers": len(active),
            "priced_member_tickers": len(priced),
            "member_price_coverage": coverage,
            "coverage_pct": round(100.0 * coverage, 2) if coverage is not None else None,
            "min_price_coverage": min_coverage,
            "benchmark_priced": benchmark_priced,
            "news_attention_published": news_published,
            "attention_degraded": attention_degraded,
            "attention_degraded_themes": degraded_themes,
            "attention_day_reasons": list(diagnostics.get("attention_day_reasons") or []),
            "theme_rows": len(result.theme_rows),
            "member_rows": len(result.member_rows),
            "company_rows": len(result.company_rows),
            "scoring": diagnostics,
        }
    )
    return QualityReport(
        passed=not errors,
        metrics=_json_safe(report_metrics),
        errors=errors,
        warnings=warnings,
    )


def backfill_date_facts(
    result: ScoringResult,
    dates: Sequence[date],
    *,
    as_of: date,
    spans: Sequence[MemberSpan],
    close: pd.DataFrame,
    news_dates: Iterable[date],
) -> dict[str, dict[str, Any]]:
    """Per-date quality facts (``published_date_facts`` shape) for backfilled dates.

    Same rules as the target's report, computed on each date's own rows and
    members: rankable rows decide ``attention_degraded``; coverage counts the
    members scored that day. Coverage below the gate only warns here: the gate
    applies to the target.
    """
    min_coverage = float(settings.PIPELINE_MIN_THEME_PRICE_COVERAGE)
    news = set(news_dates)
    rows_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in result.theme_rows:
        if row.get("rankable"):
            rows_by_date[row["date"]].append(row)
    spans_by_slug: dict[str, list[MemberSpan]] = defaultdict(list)
    for span in spans:
        spans_by_slug[span.theme_slug].append(span)
    finite = close.notna().to_numpy() if len(close.columns) else None
    position = {day: index for index, day in enumerate(close.index)}
    columns = list(close.columns)

    facts: dict[str, dict[str, Any]] = {}
    for day in dates:
        rankable = rows_by_date.get(day, [])
        degraded = sorted(row["theme_slug"] for row in rankable if row.get("attention_degraded"))
        reasons = sorted({
            str(reason)
            for row in rankable
            if row.get("attention_degraded")
            for reason in ((row.get("flags") or {}).get("attention_degraded_reasons") or [])
        })
        active = _active_member_tickers(spans_by_slug, day, as_of)
        if finite is not None and day in position:
            priced_today = {columns[index] for index in np.flatnonzero(finite[position[day]])}
        else:
            priced_today = set()
        priced = active & priced_today
        coverage = len(priced) / len(active) if active else None
        news_published = day in news
        warnings: list[str] = []
        if degraded:
            warnings.extend(_attention_warnings(day, len(degraded), reasons, news_published))
        if coverage is None:
            warnings.append(f"No active theme members on {day.isoformat()}")
        elif coverage < min_coverage:
            warnings.append(
                f"Theme member price coverage {coverage:.1%} on {day.isoformat()} is below {min_coverage:.0%}"
            )
        facts[day.isoformat()] = {
            "rankable_themes": len(rankable),
            "attention_degraded": bool(degraded),
            "attention_degraded_themes": degraded,
            "attention_reasons": reasons,
            "news_attention_published": news_published,
            "active_member_tickers": len(active),
            "priced_member_tickers": len(priced),
            "member_price_coverage": coverage,
            "coverage_pct": round(100.0 * coverage, 2) if coverage is not None else None,
            "warnings": warnings,
        }
    return _json_safe(facts)


def _target_date_facts(report: QualityReport) -> dict[str, Any]:
    """The target's ``metrics.dates`` entry: the run-level metrics and warnings."""
    metrics = report.metrics
    return {
        "rankable_themes": metrics.get("rankable_themes"),
        "attention_degraded": bool(metrics.get("attention_degraded")),
        "attention_degraded_themes": list(metrics.get("attention_degraded_themes") or []),
        "attention_reasons": list(metrics.get("attention_day_reasons") or []),
        "news_attention_published": bool(metrics.get("news_attention_published")),
        "active_member_tickers": metrics.get("active_member_tickers"),
        "priced_member_tickers": metrics.get("priced_member_tickers"),
        "member_price_coverage": metrics.get("member_price_coverage"),
        "coverage_pct": metrics.get("coverage_pct"),
        "warnings": list(report.warnings),
    }


# ---------------------------------------------------------------------------
# Row conversion and publication
# ---------------------------------------------------------------------------


def _column_names(model) -> frozenset[str]:
    return frozenset(column.key for column in model.__table__.columns)


def _rows_for_insert(
    rows: Iterable[dict[str, Any]],
    model,
    run_id: int,
    theme_ids: Optional[dict[str, int]] = None,
    extra: Optional[dict[str, Any]] = None,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Attach run/theme ids; keep only ORM columns (unknown keys are reported, not inserted)."""
    columns = _column_names(model)
    out: list[dict[str, Any]] = []
    ignored: set[str] = set()
    for row in rows:
        values = {key: value for key, value in row.items() if key in columns}
        ignored.update(key for key in row if key not in columns and key != "theme_slug")
        if theme_ids is not None:
            slug = row.get("theme_slug")
            if slug not in theme_ids:
                raise ValueError(f"Scored theme {slug!r} has no themes row")
            values["theme_id"] = theme_ids[slug]
        values["source_run_id"] = run_id
        if extra:
            values.update(extra)
        values.pop("id", None)
        out.append(values)
    return out, ignored


async def _insert_chunks(db: AsyncSession, model, rows: list[dict[str, Any]]) -> None:
    for start in range(0, len(rows), WRITE_CHUNK):
        await db.execute(insert(model), rows[start:start + WRITE_CHUNK])


async def _dates_with_published_detail(db: AsyncSession, dates: Sequence[date]) -> set[date]:
    """Dates whose current ``theme_trends`` publication has member or company rows.

    A run writes member and trending-company rows for its own target only, so a
    backfill that republished such a date would leave it without them for good.
    """
    has_members = select(ThemeMemberDaily.id).where(
        ThemeMemberDaily.source_run_id == DataPublication.pipeline_run_id,
        ThemeMemberDaily.date == DataPublication.as_of_date,
    ).exists()
    has_companies = select(CompanyTrendDaily.id).where(
        CompanyTrendDaily.source_run_id == DataPublication.pipeline_run_id,
        CompanyTrendDaily.date == DataPublication.as_of_date,
    ).exists()
    found: set[date] = set()
    for start in range(0, len(dates), QUERY_CHUNK):
        chunk = list(dates[start:start + QUERY_CHUNK])
        result = await db.execute(
            select(DataPublication.as_of_date)
            .join(PipelineRun, PipelineRun.id == DataPublication.pipeline_run_id)
            .where(
                DataPublication.dataset == THEME_TRENDS_DATASET,
                DataPublication.as_of_date.in_(chunk),
                DataPublication.status == "published",
                PipelineRun.status == "published",
                or_(has_members, has_companies),
            )
        )
        found.update(result.scalars())
    return found


async def _sweep_superseded(db: AsyncSession, dates: Sequence[date], run_id: int) -> int:
    """Delete rows of earlier runs for the dates this run just (re)published."""
    removed = 0
    for model in (ThemeDaily, ThemeMemberDaily, CompanyTrendDaily):
        for start in range(0, len(dates), QUERY_CHUNK):
            chunk = list(dates[start:start + QUERY_CHUNK])
            result = await db.execute(
                delete(model).where(model.date.in_(chunk), model.source_run_id != run_id)
            )
            removed += max(result.rowcount or 0, 0)
    return removed


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def refresh_theme_trends(target: date, *, backfill_sessions: int = 0, overwrite: bool = False) -> dict:
    """Score every non-retired theme through ``target`` and publish ``theme_trends``.

    ``backfill_sessions > 0`` emits theme rows for that many trailing sessions under
    one run and publishes each of those dates (rows computed with membership that did
    not exist yet carry ``pit=False``); member and company rows cover ``target`` only.
    An earlier date whose current publication has member or company rows (a daily
    run's) keeps that publication unless ``overwrite=True``, which republishes it and
    so deletes those rows. A kept date's served rows pin its session in scoring, so
    the dates published after it continue them; the run fails if the kept dates
    change before its write. ``metrics.dates`` records each published date's own
    quality facts (see ``published_date_facts``).
    """
    target = _session_on_or_before(target)
    backfill_sessions = max(0, int(backfill_sessions or 0))

    async with async_session_maker() as dependency_db:
        missing = [
            dataset for dataset in EXACT_DEPENDENCIES
            if await _published_run_for_date(dependency_db, dataset, target) is None
        ]
        membership_as_of = await _latest_publication_on_or_before(dependency_db, THEME_MEMBERSHIP_DATASET, target)
        if membership_as_of is None:
            missing.append(THEME_MEMBERSHIP_DATASET)
    if missing:
        logger.info("Deferring theme trends for %s: missing %s", target, ", ".join(missing))
        return {
            "status": "deferred",
            "reason": "missing-publications",
            "missing": missing,
            "as_of_date": target.isoformat(),
        }

    rerun_reason: Optional[str] = None
    async with async_session_maker() as existing_db:
        existing_run = await _published_run_for_date(existing_db, THEME_TRENDS_DATASET, target)
        if existing_run is not None and backfill_sessions == 0:
            has_rows = await existing_db.scalar(
                select(ThemeDaily.id)
                .where(ThemeDaily.source_run_id == existing_run, ThemeDaily.date == target)
                .limit(1)
            )
            if has_rows is not None:
                if not await _attention_rerun_due(existing_db, existing_run, target):
                    return {
                        "status": "skipped",
                        "reason": "already-published",
                        "as_of_date": target.isoformat(),
                        "run_id": existing_run,
                    }
                rerun_reason = "news-attention-published"
                logger.info("Re-running theme trends for %s: news attention is now published", target)

    sessions = market_sessions_through(target, SCORING_SESSIONS + backfill_sessions)
    async with async_session_maker() as source_db:
        snapshot_date, universe = await load_theme_universe(source_db, target)
        themes = list(
            (
                await source_db.execute(
                    select(Theme).where(Theme.status != "retired").order_by(Theme.slug)
                )
            ).scalars()
        )
        spans: list[MemberSpan] = await load_member_spans(source_db)
        inception = await load_proxy_inception(source_db)
        output_start = sessions[-max(1, backfill_sessions)]
        news_dates = await _published_dates_between(source_db, NEWS_ATTENTION_DATASET, output_start, target)
        output_index = sessions.index(output_start)
        earlier_output = [day for day in sessions[output_index:] if day != target]
        kept_before = set() if overwrite else await _dates_with_published_detail(source_db, earlier_output)
        # The sessions before the window plus the earlier dates this run keeps: both are
        # served as they are, so the dates it publishes must continue them.
        published_history = await _published_theme_history(
            source_db,
            [*sessions[max(0, output_index - PUBLISHED_HISTORY_SESSIONS):output_index], *sorted(kept_before)],
            {theme.id: theme.slug for theme in themes},
        )
        news_published = target in news_dates
        metas = _theme_metas(themes, inception)
        proxies = list(dict.fromkeys(etf for meta in metas for etf in meta.proxy_etfs))
        tickers = sorted(set(universe) | {span.ticker for span in spans})
        price_tickers = sorted(set(tickers) | {BENCHMARK_TICKER} | set(proxies))
        price_rows = await _load_price_rows(source_db, price_tickers, sessions[0], target)
        attention = await load_attention(
            source_db, sessions[0] - timedelta(days=ATTENTION_LEAD_DAYS), target, tickers=tickers
        )
    theme_ids = {theme.slug: theme.id for theme in themes}
    taxonomy_versions = Counter(theme.taxonomy_version for theme in themes if theme.taxonomy_version)

    close, volume, benchmark, proxy_close = await asyncio.to_thread(
        build_price_frames, price_rows, sessions, tickers, benchmark=BENCHMARK_TICKER, proxies=proxies
    )
    price_row_count = len(price_rows)
    del price_rows
    active_tickers = sorted({span.ticker for span in spans if span.active_on(target)})
    target_close = close.loc[target] if len(close.index) else pd.Series(dtype=float)
    priced_tickers = [ticker for ticker in active_tickers if np.isfinite(target_close.get(ticker, np.nan))]
    benchmark_priced = bool(np.isfinite(benchmark.get(target, np.nan)))
    inputs = ScoringInputs(
        as_of=target,
        sessions=list(sessions),
        close=close,
        volume=volume,
        benchmark=benchmark,
        members=spans,
        themes=metas,
        industries={ticker: profile.industry for ticker, profile in universe.items() if profile.industry},
        sectors={ticker: profile.sector for ticker, profile in universe.items() if profile.sector},
        names={ticker: profile.name for ticker, profile in universe.items() if profile.name},
        market_caps={
            ticker: profile.market_cap for ticker, profile in universe.items() if profile.market_cap is not None
        },
        universe=frozenset(universe),
        attention_counts=attention.counts,
        attention_totals=attention.totals,
        proxy_close=proxy_close,
        output_sessions=max(1, backfill_sessions),
    )
    base_metrics: dict[str, Any] = {
        "scoring_version": SCORING_VERSION,
        "taxonomy_version": taxonomy_versions.most_common(1)[0][0] if taxonomy_versions else None,
        "universe_snapshot_date": snapshot_date,
        "universe_size": len(universe),
        "membership_as_of": membership_as_of,
        "themes": len(themes),
        "member_spans": len(spans),
        "sessions": len(sessions),
        "window_start": sessions[0],
        "backfill_sessions": backfill_sessions,
        "price_rows": price_row_count,
        "price_tickers": len(price_tickers),
        "proxy_etfs": len(proxies),
        "attention_complete_days": len(attention.complete_days),
        "rerun_reason": rerun_reason,
        "published_history_rows": len(published_history),
        "pinned_kept_dates": len(kept_before),
    }

    run_id = await begin_pipeline_run(THEME_TRENDS_PIPELINE, target, version=SCORING_VERSION)
    report: Optional[QualityReport] = None
    try:
        await update_pipeline_run(run_id, "scoring_themes")
        result = await asyncio.to_thread(score_themes, inputs, published=published_history, pin_output_dates=True)
        del inputs
        report = evaluate_theme_trends(
            result,
            target=target,
            active_tickers=active_tickers,
            priced_tickers=priced_tickers,
            benchmark_priced=benchmark_priced,
            news_published=news_published,
            metrics=base_metrics,
        )
        if not universe:
            report.warnings.append(f"No screener universe for {target.isoformat()}; trending companies are empty")
        if not report.passed:
            raise DataQualityError(report)

        theme_values, ignored_theme = _rows_for_insert(
            result.theme_rows, ThemeDaily, run_id, theme_ids, extra={"scoring_version": SCORING_VERSION}
        )
        member_values, ignored_member = _rows_for_insert(result.member_rows, ThemeMemberDaily, run_id, theme_ids)
        company_values, ignored_company = _rows_for_insert(result.company_rows, CompanyTrendDaily, run_id)
        ignored = sorted(ignored_theme | ignored_member | ignored_company)
        if ignored:
            report.warnings.append("Scoring keys without columns were not stored: " + ", ".join(ignored))
        target_values = [row for row in theme_values if row["date"] == target]
        earlier_values = [row for row in theme_values if row["date"] != target]
        earlier_dates = sorted({row["date"] for row in earlier_values})
        earlier_facts = await asyncio.to_thread(
            backfill_date_facts,
            result,
            earlier_dates,
            as_of=target,
            spans=spans,
            close=close,
            news_dates=news_dates,
        )
        del close, volume
        target_facts = _target_date_facts(report)
        await update_pipeline_run(
            run_id, "publishing_theme_trends", len(theme_values) + len(member_values) + len(company_values)
        )
        async with async_session_maker() as db, db.begin():
            # Target rows first: that first write takes SQLite's write lock, so the
            # check of earlier dates' publications below cannot race another writer.
            await _insert_chunks(db, ThemeDaily, target_values)
            await _insert_chunks(db, ThemeMemberDaily, member_values)
            await _insert_chunks(db, CompanyTrendDaily, company_values)
            kept = set() if overwrite else await _dates_with_published_detail(db, earlier_output)
            if kept != kept_before:
                # Another writer changed an earlier date's publication since the source
                # read: the rows this run's scoring continued are no longer the served ones.
                changed = ", ".join(day.isoformat() for day in sorted(kept ^ kept_before))
                raise RuntimeError(f"theme_trends publications changed during the run ({changed}); re-run")
            fill_values = [row for row in earlier_values if row["date"] not in kept]
            await _insert_chunks(db, ThemeDaily, fill_values)
            published_dates = sorted({row["date"] for row in fill_values} | {target})
            date_facts = {
                day.isoformat(): target_facts if day == target else earlier_facts[day.isoformat()]
                for day in published_dates
            }
            records = len(target_values) + len(fill_values) + len(member_values) + len(company_values)
            report.metrics.update(
                {
                    "published_dates": len(published_dates),
                    "first_published_date": published_dates[0].isoformat(),
                    "kept_dates": [day.isoformat() for day in sorted(kept)],
                    "overwrite": overwrite,
                    "dates_attention_degraded": sum(1 for facts in date_facts.values() if facts["attention_degraded"]),
                    "dates": date_facts,
                }
            )
            published_at = utc_now()
            for day in published_dates:
                if day != target:
                    await publish_dataset(THEME_TRENDS_DATASET, day, run_id, db=db, published_at=published_at)
            await publish_datasets_and_finish(
                db,
                [THEME_TRENDS_DATASET],
                target,
                run_id,
                quality_report=report.to_dict(),
                records_processed=records,
            )
            swept = await _sweep_superseded(db, published_dates, run_id)
        logger.info(
            "Theme trends published for %s (run %s): %s rankable themes, %s dates (%s kept), %s rows, "
            "%s superseded rows swept%s",
            target,
            run_id,
            report.metrics["rankable_themes"],
            len(published_dates),
            len(kept),
            records,
            swept,
            " (attention degraded)" if report.metrics["attention_degraded"] else "",
        )
        return {
            "status": "published",
            "run_id": run_id,
            "as_of_date": target.isoformat(),
            "rankable": report.metrics["rankable_themes"],
            "themes": len(themes),
            "published_dates": len(published_dates),
            "first_published_date": published_dates[0].isoformat(),
            "kept_dates": len(kept),
            "dates_attention_degraded": report.metrics["dates_attention_degraded"],
            "theme_rows": len(target_values) + len(fill_values),
            "member_rows": len(member_values),
            "company_rows": len(company_values),
            "coverage_pct": report.metrics["coverage_pct"],
            "attention_degraded": report.metrics["attention_degraded"],
            "swept_rows": swept,
            "rerun_reason": rerun_reason,
            "warnings": list(report.warnings),
        }
    except asyncio.CancelledError:
        await finish_pipeline_run(run_id, "cancelled", error_message="Pipeline execution was cancelled")
        raise
    except Exception as exc:
        quality = exc.report.to_dict() if isinstance(exc, DataQualityError) else (
            report.to_dict() if report is not None else None
        )
        logger.warning("Theme trends failed for %s (run %s): %s", target, run_id, exc)
        await finish_pipeline_run(run_id, "failed", quality_report=quality, error_message=str(exc))
        raise
