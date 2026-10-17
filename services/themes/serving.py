"""Read-only theme APIs: local published data only (no provider or LLM calls).

Rows follow the factor publication pattern: a ``theme_daily`` row is served only
when ``DataPublication(theme_trends, row.date).pipeline_run_id`` is the run that
wrote it and that run is published. Stored log returns are converted to simple
fractions here (``exp(x) - 1``); missing values stay ``None``.

Reads are not snapshot-isolated (SQLite issues no BEGIN for SELECTs; PostgreSQL
defaults to READ COMMITTED), and a same-date re-run repoints the publication and
sweeps the old run's rows in one transaction. Each endpoint therefore re-checks
the as-of publication after reading and re-reads once it moved (``_read_publication``).
"""

from __future__ import annotations

import math
import re
import textwrap
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Awaitable, Callable, Iterable, Optional

import yaml
from sqlalchemy import and_, case, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.time_utils import utc_now
from core.trading_calendar import NEW_YORK, is_us_market_session, latest_completed_us_session
from models import (
    CompanyTrendDaily,
    DailyPrice,
    DataPublication,
    PipelineRun,
    StockScreenerSnapshot,
    Theme,
    ThemeDaily,
    ThemeMember,
    ThemeMemberDaily,
    ThemeProposal,
    Ticker,
)
from services.themes import briefs as theme_briefs
from services.themes.pipeline import THEME_TRENDS_DATASET, published_date_facts
from services.themes.taxonomy import SLUG_PATTERN, load_taxonomy
from services.themes.universe import latest_screener_publication_on_or_before

BENCHMARK_TICKER = "SPY.US"
HEAT_SPARKLINE_SESSIONS = 60
DETAIL_HISTORY_SESSIONS = 120
# The theme_trends jobs run at 04:50 and 07:50 ET; before 06:00 ET the session
# that closed yesterday is not expected yet, so "today" is still yesterday.
STALE_REFERENCE_CUTOFF_ET = time(6, 0)
# A read that keeps racing republications gives up with a 503 instead of mixing runs.
_READ_ATTEMPTS = 3
# (k, column): stored k-session log excess returns used to bridge unpublished sessions.
_EXCESS_WINDOWS = ((5, "x5"), (21, "x21"), (63, "x63"), (126, "x126"))
_QUERY_CHUNK = 500


class ThemeTrendsUnavailable(RuntimeError):
    """No theme_trends publication exists (API -> 503)."""


class ThemeTrendsDateUnavailable(ThemeTrendsUnavailable):
    """Publications exist, but none for the requested as_of_date (API -> 404)."""


class ThemeNotFound(LookupError):
    """Unknown or retired theme slug (API -> 404)."""


class ThemeProposalNotFound(LookupError):
    """Unknown proposal id (API -> 404)."""


# ---------------------------------------------------------------------------
# Value conversions
# ---------------------------------------------------------------------------


def _finite(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _simple_return(log_value: Any) -> Optional[float]:
    """Log (excess) return -> simple fraction; 0.05 == +5%."""
    number = _finite(log_value)
    if number is None:
        return None
    try:
        return math.expm1(number)
    except OverflowError:
        return None


def _exp(log_value: Any) -> Optional[float]:
    number = _finite(log_value)
    if number is None:
        return None
    try:
        return math.exp(number)
    except OverflowError:
        return None


def _optional_int(value: Any) -> Optional[int]:
    return int(value) if value is not None else None


def _iso(value: Optional[date | datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _as_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _float_list(values: Any) -> list[Optional[float]]:
    return [_finite(value) for value in values] if isinstance(values, list) else []


def _string_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        if isinstance(value, str):
            result.append(value)
        elif isinstance(value, dict):
            label = value.get("source") or value.get("channel") or value.get("type")
            if label:
                result.append(str(label))
    return result


def _chunks(values: list[str]) -> Iterable[list[str]]:
    for start in range(0, len(values), _QUERY_CHUNK):
        yield values[start:start + _QUERY_CHUNK]


# ---------------------------------------------------------------------------
# Publication selection and metadata
# ---------------------------------------------------------------------------


def expected_theme_session(now_utc: Optional[datetime] = None) -> date:
    """Latest session a current theme_trends publication should cover (06:00 ET cutoff)."""
    now_et = (now_utc or utc_now()).replace(tzinfo=timezone.utc).astimezone(NEW_YORK)
    reference = now_et.date()
    if now_et.time() < STALE_REFERENCE_CUTOFF_ET:
        reference -= timedelta(days=1)
    return latest_completed_us_session(reference)


def _published_publications(*columns):
    return (
        select(*(columns or (DataPublication,)))
        .join(PipelineRun, PipelineRun.id == DataPublication.pipeline_run_id)
        .where(
            DataPublication.dataset == THEME_TRENDS_DATASET,
            DataPublication.status == "published",
            PipelineRun.status == "published",
        )
    )


def _published_theme_rows(*columns):
    """Select theme_daily columns, keeping only each date's published run."""
    return (
        select(*columns)
        .join(
            DataPublication,
            and_(
                DataPublication.dataset == THEME_TRENDS_DATASET,
                DataPublication.as_of_date == ThemeDaily.date,
                DataPublication.pipeline_run_id == ThemeDaily.source_run_id,
            ),
        )
        .join(PipelineRun, PipelineRun.id == DataPublication.pipeline_run_id)
        .where(DataPublication.status == "published", PipelineRun.status == "published")
    )


async def _select_publication(db: AsyncSession, as_of_date: Optional[date]) -> DataPublication:
    # populate_existing: a re-run updates the publication row in place, and a re-read
    # must not hand back the stale run id cached in the session's identity map.
    query = _published_publications().execution_options(populate_existing=True)
    if as_of_date is not None:
        query = query.where(DataPublication.as_of_date == as_of_date)
    publication = (
        await db.execute(query.order_by(desc(DataPublication.as_of_date)).limit(1))
    ).scalars().first()
    if publication is not None:
        return publication
    if as_of_date is not None:
        any_published = (await db.execute(_published_publications().limit(1))).scalars().first()
        if any_published is not None:
            raise ThemeTrendsDateUnavailable(
                f"Theme trends are not published for {as_of_date.isoformat()}"
            )
    raise ThemeTrendsUnavailable("Theme trends have not been published yet")


async def _published_run_id(db: AsyncSession, as_of: date) -> Optional[int]:
    return (
        await db.execute(
            _published_publications(DataPublication.pipeline_run_id).where(DataPublication.as_of_date == as_of)
        )
    ).scalars().first()


async def _read_publication(
    db: AsyncSession,
    as_of_date: Optional[date],
    build: Callable[[DataPublication], Awaitable[dict]],
) -> dict:
    """``build(publication)``, re-read when that session was republished meanwhile.

    The publication is repointed in the same transaction that sweeps the old
    run's rows, so if it still names the same run after ``build`` returned, every
    statement ``build`` ran saw that run's rows intact. ``ThemeNotFound`` from a
    swept read ("no published trends") is retried the same way.
    """
    for _ in range(_READ_ATTEMPTS):
        publication = await _select_publication(db, as_of_date)
        run_id, as_of = publication.pipeline_run_id, publication.as_of_date
        try:
            payload = await build(publication)
        except ThemeNotFound:
            if await _published_run_id(db, as_of) == run_id:
                raise
            continue
        if await _published_run_id(db, as_of) == run_id:
            return payload
    raise ThemeTrendsUnavailable("Theme trends were republished while being read; retry shortly")


def _coverage_pct(quality: dict, metrics: dict) -> Optional[float]:
    """Active-member price coverage on as_of, in percent (0-100).

    The pipeline stores ``metrics.coverage_pct`` in percent next to the fraction
    ``metrics.member_price_coverage`` (0-1); the fraction is the fallback.
    """
    percent = _finite(metrics.get("coverage_pct", quality.get("coverage_pct")))
    if percent is not None:
        return percent
    fraction = _finite(metrics.get("member_price_coverage"))
    return 100.0 * fraction if fraction is not None else None


async def _publication_context(
    db: AsyncSession,
    publication: DataPublication,
) -> tuple[dict, list[tuple[ThemeDaily, Theme]]]:
    """Meta for one publication plus every theme row of its as-of session."""
    result = await db.execute(
        select(ThemeDaily, Theme)
        .join(Theme, Theme.id == ThemeDaily.theme_id)
        .where(
            ThemeDaily.source_run_id == publication.pipeline_run_id,
            ThemeDaily.date == publication.as_of_date,
        )
    )
    pairs = [(row, theme) for row, theme in result.all()]
    run = await db.get(PipelineRun, publication.pipeline_run_id)
    quality = (run.quality_report if run else None) or {}
    metrics = quality.get("metrics") or {}
    # A backfill run publishes many dates; each date's own facts live in
    # metrics.dates, while the top-level metrics describe the run's target.
    per_date = (metrics.get("dates") or {}).get(publication.as_of_date.isoformat())
    facts = published_date_facts(quality, publication.as_of_date) if isinstance(per_date, dict) else None
    expected = expected_theme_session()
    taxonomy_versions = Counter(
        theme.taxonomy_version for _, theme in pairs if theme.status != "retired" and theme.taxonomy_version
    )
    scoring_version = next((row.scoring_version for row, _ in pairs if row.scoring_version), None)
    # A theme without members has no basket (scoring marks every such row pit=False,
    # e.g. a theme added after the last membership refresh), so it says nothing about
    # whether the served baskets used point-in-time membership; retired themes are not served.
    baskets = [row for row, theme in pairs if theme.status != "retired" and (row.n_members or 0) > 0]
    meta = {
        "as_of_date": publication.as_of_date.isoformat(),
        "expected_as_of_date": expected.isoformat(),
        "published_at": _iso(publication.published_at),
        "stale": publication.as_of_date < expected,
        "scoring_version": scoring_version or (run.version if run else ""),
        "taxonomy_version": metrics.get("taxonomy_version")
        or (taxonomy_versions.most_common(1)[0][0] if taxonomy_versions else None),
        "point_in_time": all(row.pit for row in baskets),
        "attention_degraded": bool(
            facts.get("attention_degraded") if facts is not None
            else metrics.get("attention_degraded", quality.get("attention_degraded", False))
        ),
        "coverage_pct": _coverage_pct(facts, facts) if facts is not None else _coverage_pct(quality, metrics),
        "warnings": list(dict.fromkeys(
            str(item) for item in ((facts.get("warnings") if facts is not None else quality.get("warnings")) or [])
        )),
    }
    return meta, pairs


async def _company_names(db: AsyncSession, tickers: Iterable[str], as_of: date) -> dict[str, str]:
    """Display names: screener snapshot <= as_of first, then ``tickers.name``."""
    wanted = sorted({ticker for ticker in tickers if ticker})
    if not wanted:
        return {}
    names: dict[str, str] = {}
    snapshot_date = await latest_screener_publication_on_or_before(db, as_of)
    for chunk in _chunks(wanted):
        if snapshot_date is not None:
            rows = await db.execute(
                select(StockScreenerSnapshot.ticker, StockScreenerSnapshot.name).where(
                    StockScreenerSnapshot.date == snapshot_date,
                    StockScreenerSnapshot.ticker.in_(chunk),
                )
            )
            names.update({ticker: name for ticker, name in rows.all() if name})
        missing = [ticker for ticker in chunk if ticker not in names]
        if missing:
            rows = await db.execute(select(Ticker.ticker, Ticker.name).where(Ticker.ticker.in_(missing)))
            names.update({ticker: name for ticker, name in rows.all() if name})
    return names


# ---------------------------------------------------------------------------
# Theme rows
# ---------------------------------------------------------------------------


def _leader_tickers(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    tickers = []
    for item in value:
        ticker = item.get("ticker") if isinstance(item, dict) else item
        if isinstance(ticker, str) and ticker:
            tickers.append(ticker)
    return tickers


async def _heat_sparklines(db: AsyncSession, theme_ids: list[int], as_of: date) -> dict[int, list[Optional[float]]]:
    """heat_now over the last 60 published dates <= as_of (one row per date)."""
    if not theme_ids:
        return {}
    dates = list(
        (
            await db.execute(
                _published_publications(DataPublication.as_of_date)
                .where(DataPublication.as_of_date <= as_of)
                .order_by(desc(DataPublication.as_of_date))
                .limit(HEAT_SPARKLINE_SESSIONS)
            )
        ).scalars()
    )
    if not dates:
        return {}
    rows = await db.execute(
        _published_theme_rows(ThemeDaily.theme_id, ThemeDaily.heat_now)
        .where(
            ThemeDaily.theme_id.in_(theme_ids),
            ThemeDaily.date >= min(dates),
            ThemeDaily.date <= as_of,
        )
        .order_by(ThemeDaily.date.asc())
    )
    sparklines: dict[int, list[Optional[float]]] = defaultdict(list)
    for theme_id, heat_now in rows.all():
        sparklines[theme_id].append(_finite(heat_now))
    return sparklines


def _theme_item(
    row: ThemeDaily,
    theme: Theme,
    names: dict[str, str],
    sparkline: list[Optional[float]],
) -> dict:
    return {
        "slug": theme.slug,
        "name_en": theme.name_en,
        "name_zh": theme.name_zh,
        "megatrend": theme.megatrend,
        "stage": row.stage,
        "candidate_stage": row.candidate_stage,
        "stage_changed": bool(row.stage_changed),
        "heat": _finite(row.heat),
        "heat_now": _finite(row.heat_now),
        "heat_trend": _finite(row.heat_trend),
        "rank_now": _optional_int(row.rank_now),
        "rank_trend": _optional_int(row.rank_trend),
        "rank_change": _optional_int(row.rank_change),
        "streak_top5": row.streak_top5 or 0,
        "score_price": _finite(row.score_price),
        "score_breadth": _finite(row.score_breadth),
        "score_volume": _finite(row.score_volume),
        "score_attention": _finite(row.score_attention),
        "excess_5d": _simple_return(row.x5),
        "excess_21d": _simple_return(row.x21),
        "excess_63d": _simple_return(row.x63),
        "pct_above_ma50": _finite(row.b50),
        "near_high_share": _finite(row.nh),
        "pct_above_ma50_change_10d": _finite(row.d_b50),
        "ignitions_today": row.ign or 0,
        "ignitions_5d": row.ign5 or 0,
        "n_members": row.n_members or 0,
        "n_priced": row.n_priced or 0,
        "leaders": [
            {"ticker": ticker, "name": names.get(ticker)} for ticker in _leader_tickers(row.leaders)
        ],
        "proxy_etf": row.proxy_etf,
        "proxy_excess_21d": _simple_return(row.proxy_x21),
        "heat_sparkline": sparkline,
        "emerging": bool(row.emerge),
        "attention_only": bool(row.attention_only),
        "attention_degraded": bool(row.attention_degraded),
        "point_in_time": bool(row.pit),
    }


async def _theme_items(db: AsyncSession, pairs: list[tuple[ThemeDaily, Theme]], as_of: date) -> list[dict]:
    names = await _company_names(db, (t for row, _ in pairs for t in _leader_tickers(row.leaders)), as_of)
    sparklines = await _heat_sparklines(db, [theme.id for _, theme in pairs], as_of)
    return [_theme_item(row, theme, names, sparklines.get(theme.id, [])) for row, theme in pairs]


def _rank_sort_key(item: dict, view: str) -> tuple:
    rank = item["rank_now"] if view == "now" else item["rank_trend"]
    return (rank is None, rank if rank is not None else 0, item["slug"])


def _summary(items: list[dict], pairs: list[tuple[ThemeDaily, Theme]]) -> dict:
    ranked = [item for item, (row, _) in zip(items, pairs) if row.rankable]
    stages = Counter(item["stage"] for item in ranked)
    risers = [item for item in items if (item["rank_change"] or 0) > 0]
    top_riser = min(
        risers,
        key=lambda item: (
            -item["rank_change"],
            item["rank_now"] if item["rank_now"] is not None else math.inf,
            item["slug"],
        ),
        default=None,
    )
    return {
        "ranked": len(ranked),
        "emerging": stages["emerging"],
        "accelerating": stages["accelerating"],
        "leading": stages["leading"],
        "crowded": stages["crowded"],
        "fading": stages["fading"],
        "top_riser": top_riser["slug"] if top_riser else None,
    }


async def get_theme_list(db: AsyncSession, *, as_of_date: Optional[date] = None, view: str = "now",
                         stage: Optional[str] = None) -> dict:
    if view not in ("now", "trend"):
        raise ValueError(f"Unsupported theme view: {view}")

    async def build(publication: DataPublication) -> dict:
        meta, pairs = await _publication_context(db, publication)
        pairs = [(row, theme) for row, theme in pairs if theme.status != "retired"]
        items = await _theme_items(db, pairs, publication.as_of_date)
        summary = _summary(items, pairs)
        if stage is not None:
            items = [item for item in items if item["stage"] == stage]
        items.sort(key=lambda item: _rank_sort_key(item, view))
        return {"meta": meta, "view": view, "summary": summary, "themes": items}

    return await _read_publication(db, as_of_date, build)


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def _sessions_between(start: date, end: date, extra: Iterable[date] = ()) -> list[date]:
    """US sessions in [start, end] (plus ``extra`` dates), ascending."""
    sessions = set(extra)
    current = start
    while current <= end:
        if is_us_market_session(current):
            sessions.add(current)
        current += timedelta(days=1)
    return sorted(sessions)


def _basket_series(
    rows: list[ThemeDaily],
    benchmark: Optional[dict[date, float]] = None,
) -> tuple[list[Optional[float]], list[date]]:
    """Basket level rebased to 1.0 at the first returned date, plus the dates it cannot measure.

    Every run's ``basket_index`` starts at 1.0 on its own scoring window, so levels
    from different runs are not comparable. A step to a row is measured, in order:
    by the index ratio when the previous row is the same run; by the row's 1-session
    ``ret_1d`` when the previous row is the previous US session; or by bridging from
    the level k sessions back with the row's ``x_k`` (log excess vs the benchmark)
    plus the benchmark's own return. A step none of these measures (a session that
    was never published, or missing values) leaves that date ``None`` and assumes the
    basket tracked the benchmark across it, so later dates keep the excess earned
    before the gap instead of silently dropping the gap's return.
    """
    benchmark = benchmark or {}
    if not rows:
        return [], []
    sessions = _sessions_between(rows[0].date, rows[-1].date, (row.date for row in rows))
    position = {session: index for index, session in enumerate(sessions)}
    measured: dict[date, float] = {}
    unmeasured: list[date] = []
    series: list[Optional[float]] = []
    level = 1.0
    previous: Optional[ThemeDaily] = None
    for row in rows:
        if previous is None:
            known = _finite(row.basket_index) is not None or _finite(row.ret_1d) is not None
            series.append(level if known else None)
            if known:
                measured[row.date] = level
            previous = row
            continue
        current = _finite(row.basket_index)
        prior = _finite(previous.basket_index)
        ret_1d = _finite(row.ret_1d)
        here = position[row.date]
        new_level = None
        if previous.source_run_id == row.source_run_id and current is not None and prior is not None and prior > 0:
            new_level = level * current / prior
        elif ret_1d is not None and position[previous.date] == here - 1:
            new_level = level * (1.0 + ret_1d)
        else:
            for sessions_back, column in _EXCESS_WINDOWS:
                growth = _exp(getattr(row, column))
                if growth is None or here < sessions_back:
                    continue
                anchor = sessions[here - sessions_back]
                base, start, end = measured.get(anchor), benchmark.get(anchor), benchmark.get(row.date)
                if base is not None and start and end:
                    new_level = base * growth * end / start
                    break
        if new_level is not None and math.isfinite(new_level):
            level = new_level
            measured[row.date] = level
            series.append(level)
        else:
            start, end = benchmark.get(previous.date), benchmark.get(row.date)
            if start and end:
                level *= end / start
            unmeasured.append(row.date)
            series.append(None)
        previous = row
    return series, unmeasured


def _rebased(values: list[Optional[float]]) -> list[Optional[float]]:
    """Normalize to 1.0 at the first available positive value."""
    base = next((value for value in values if value is not None and value > 0), None)
    if base is None:
        return [None] * len(values)
    return [value / base if value is not None else None for value in values]


async def _closes(db: AsyncSession, tickers: list[str], dates: list[date]) -> dict[str, dict[date, float]]:
    """Adjusted close (raw close fallback) from local ``daily_prices``."""
    closes: dict[str, dict[date, float]] = defaultdict(dict)
    if not tickers or not dates:
        return closes
    rows = await db.execute(
        select(DailyPrice.ticker, DailyPrice.date, DailyPrice.adjusted_close, DailyPrice.close).where(
            DailyPrice.ticker.in_(tickers),
            DailyPrice.date >= dates[0],
            DailyPrice.date <= dates[-1],
        )
    )
    for ticker, session, adjusted_close, close in rows.all():
        value = _finite(adjusted_close if adjusted_close is not None else close)
        if value is not None:
            closes[ticker][session] = value
    return closes


async def _theme_history(db: AsyncSession, theme: Theme, as_of: date, days: int) -> tuple[dict, list[str]]:
    """The theme's published rows (last ``days`` dates <= as_of) plus meta warnings."""
    rows = list(
        (
            await db.execute(
                _published_theme_rows(ThemeDaily)
                .where(ThemeDaily.theme_id == theme.id, ThemeDaily.date <= as_of)
                .order_by(ThemeDaily.date.desc())
                .limit(days)
            )
        ).scalars()
    )
    rows.reverse()
    dates = [row.date for row in rows]
    proxy = next((row.proxy_etf for row in reversed(rows) if row.proxy_etf), None)
    if proxy is None and theme.proxy_etfs:
        proxy = theme.proxy_etfs[0]
    closes = await _closes(db, [BENCHMARK_TICKER, *([proxy] if proxy else [])], dates)
    benchmark = closes.get(BENCHMARK_TICKER, {})
    proxy_closes = closes.get(proxy, {}) if proxy else {}
    basket, unmeasured = _basket_series(rows, benchmark)
    warnings = []
    if unmeasured:
        shown = ", ".join(session.isoformat() for session in unmeasured[:5])
        more = f" and {len(unmeasured) - 5} more" if len(unmeasured) > 5 else ""
        warnings.append(
            f"{theme.slug} basket index is not measured on {shown}{more} (a session before it is "
            f"unpublished or lacks basket values); the basket is assumed to track {BENCHMARK_TICKER} there"
        )
    history = {
        "dates": [session.isoformat() for session in dates],
        "heat": [_finite(row.heat) for row in rows],
        "heat_now": [_finite(row.heat_now) for row in rows],
        "heat_trend": [_finite(row.heat_trend) for row in rows],
        "score_price": [_finite(row.score_price) for row in rows],
        "score_breadth": [_finite(row.score_breadth) for row in rows],
        "score_volume": [_finite(row.score_volume) for row in rows],
        "score_attention": [_finite(row.score_attention) for row in rows],
        "basket_index": basket,
        "benchmark_index": _rebased([benchmark.get(session) for session in dates]),
        "proxy_index": _rebased([proxy_closes.get(session) for session in dates]),
        "pct_above_ma50": [_finite(row.b50) for row in rows],
        "near_high_share": [_finite(row.nh) for row in rows],
        "ignitions": [row.ign or 0 for row in rows],
        "stage": [row.stage for row in rows],
        "rank_now": [_optional_int(row.rank_now) for row in rows],
        "point_in_time": [bool(row.pit) for row in rows],
    }
    return history, warnings


async def _active_theme(db: AsyncSession, slug: str) -> Theme:
    theme = (await db.execute(select(Theme).where(Theme.slug == slug))).scalars().first()
    if theme is None or theme.status == "retired":
        raise ThemeNotFound(f"Theme {slug} not found")
    return theme


async def get_theme_history(db: AsyncSession, slug: str, *, days: int = 252) -> dict:
    theme = await _active_theme(db, slug)

    async def build(publication: DataPublication) -> dict:
        meta, _ = await _publication_context(db, publication)
        history, warnings = await _theme_history(db, theme, publication.as_of_date, days)
        meta["warnings"].extend(warnings)
        return {"meta": meta, "slug": theme.slug, "history": history}

    return await _read_publication(db, None, build)


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


def _interval_for(intervals: list[ThemeMember], as_of: date) -> Optional[ThemeMember]:
    """The interval open on as_of, else the latest one that started by then, else the first."""
    for interval in intervals:
        if interval.valid_from <= as_of and (interval.valid_to is None or as_of < interval.valid_to):
            return interval
    started = [interval for interval in intervals if interval.valid_from <= as_of]
    if started:
        return max(started, key=lambda interval: interval.valid_from)
    return min(intervals, key=lambda interval: interval.valid_from) if intervals else None


async def _theme_members(db: AsyncSession, theme: Theme, run_id: int, as_of: date) -> list[dict]:
    member_rows = list(
        (
            await db.execute(
                select(ThemeMemberDaily).where(
                    ThemeMemberDaily.source_run_id == run_id,
                    ThemeMemberDaily.theme_id == theme.id,
                    ThemeMemberDaily.date == as_of,
                )
            )
        ).scalars()
    )
    if not member_rows:
        return []
    tickers = sorted({row.ticker for row in member_rows})
    intervals: dict[str, list[ThemeMember]] = defaultdict(list)
    for chunk in _chunks(tickers):
        result = await db.execute(
            select(ThemeMember).where(ThemeMember.theme_id == theme.id, ThemeMember.ticker.in_(chunk))
        )
        for interval in result.scalars():
            intervals[interval.ticker].append(interval)
    names = await _company_names(db, tickers, as_of)
    members = []
    for row in member_rows:
        interval = _interval_for(intervals.get(row.ticker, []), as_of)
        members.append({
            "ticker": row.ticker,
            "name": names.get(row.ticker),
            "tier": row.tier,
            "weight": float(row.weight),
            # A scored member without any interval cannot happen in a consistent DB.
            "exposure": interval.exposure if interval else 0,
            "role": interval.role if interval else None,
            "review_status": interval.review_status if interval else "unknown",
            "sources": _string_list(interval.sources) if interval else [],
            "evidence_quote": interval.evidence_quote if interval else None,
            "evidence_source": interval.evidence_source if interval else None,
            "evidence_as_of": _iso(interval.evidence_as_of) if interval else None,
            "close": _finite(row.close),
            "return_1d": _finite(row.r1),
            "excess_5d": _simple_return(row.x5),
            "excess_21d": _simple_return(row.x21),
            "near_high_ratio": _finite(row.pth),
            "relative_volume_5d": _exp(row.rvol5),
            "attention_z": _finite(row.att_z),
            "ignited": bool(row.ignited),
            "first_ignition_date": _iso(row.first_ignition_date),
            "leader_score": _finite(row.leader_score),
            "leader_rank": _optional_int(row.leader_rank),
            "is_leader": bool(row.is_leader),
            "sparkline": _float_list(row.sparkline),
        })
    members.sort(key=lambda member: (
        not member["is_leader"],
        member["leader_rank"] is None,
        member["leader_rank"] or 0,
        -member["weight"],
        member["ticker"],
    ))
    return members


async def _theme_brief(db: AsyncSession, theme: Theme, as_of: date) -> Optional[dict]:
    try:
        brief = await theme_briefs.latest_theme_brief(db, theme.id, as_of)
    except NotImplementedError:
        return None
    if not brief:
        return None
    brief_date = _as_date(brief.get("date"))
    return {**brief, "stale": brief_date is None or brief_date < as_of}


async def get_theme_detail(db: AsyncSession, slug: str, *, as_of_date: Optional[date] = None) -> dict:
    theme = await _active_theme(db, slug)

    async def build(publication: DataPublication) -> dict:
        meta, pairs = await _publication_context(db, publication)
        as_of = publication.as_of_date
        pair = next(((row, item) for row, item in pairs if item.id == theme.id), None)
        if pair is None:
            raise ThemeNotFound(f"Theme {slug} has no published trends for {as_of.isoformat()}")
        item = (await _theme_items(db, [pair], as_of))[0]
        history, warnings = await _theme_history(db, theme, as_of, DETAIL_HISTORY_SESSIONS)
        meta["warnings"].extend(warnings)
        return {
            "meta": meta,
            "theme": item,
            "definition": theme.definition,
            "keywords": list(theme.keywords or []),
            "proxy_etfs": list(theme.proxy_etfs or []),
            "history": history,
            "members": await _theme_members(db, theme, publication.pipeline_run_id, as_of),
            "brief": await _theme_brief(db, theme, as_of),
        }

    return await _read_publication(db, as_of_date, build)


# ---------------------------------------------------------------------------
# Trending companies
# ---------------------------------------------------------------------------


def _company_item(row: CompanyTrendDaily) -> dict:
    return {
        "rank": row.rank,
        "ticker": row.ticker,
        "name": row.name,
        "sector": row.sector,
        "industry": row.industry,
        "score": float(row.score),
        "close": _finite(row.close),
        "market_cap": _finite(row.market_cap),
        "return_1d": _finite(row.r1),
        "excess_5d": _simple_return(row.x5),
        "excess_5d_vs_sector": _simple_return(row.x5_sector),
        "relative_volume_5d": _exp(row.rvol5),
        "attention_z": _finite(row.att_z),
        "near_high_ratio": _finite(row.pth),
        "themes": _string_list(row.theme_slugs),
        "flags": _string_list(row.flags),
    }


async def get_trending_companies(db: AsyncSession, *, as_of_date: Optional[date] = None, limit: int = 50) -> dict:
    async def build(publication: DataPublication) -> dict:
        meta, _ = await _publication_context(db, publication)
        rows = (
            await db.execute(
                select(CompanyTrendDaily)
                .where(
                    CompanyTrendDaily.source_run_id == publication.pipeline_run_id,
                    CompanyTrendDaily.date == publication.as_of_date,
                )
                .order_by(CompanyTrendDaily.rank.asc(), CompanyTrendDaily.ticker.asc())
                .limit(limit)
            )
        ).scalars()
        return {"meta": meta, "companies": [_company_item(row) for row in rows]}

    return await _read_publication(db, as_of_date, build)


# ---------------------------------------------------------------------------
# Proposals (owner review)
# ---------------------------------------------------------------------------


def _proposal_item(proposal: ThemeProposal) -> dict:
    evidence = proposal.evidence if isinstance(proposal.evidence, list) else []
    return {
        "id": proposal.id,
        "label_en": proposal.label_en,
        "label_zh": proposal.label_zh,
        "slug_suggestion": proposal.slug_suggestion,
        "rationale": proposal.rationale,
        "tickers": _string_list(proposal.tickers),
        "evidence": [item for item in evidence if isinstance(item, dict)],
        "first_seen": proposal.first_seen.isoformat(),
        "last_seen": proposal.last_seen.isoformat(),
        "hit_count": proposal.hit_count,
        "status": proposal.status,
    }


def _proposal_slug(proposal: ThemeProposal, taken: Iterable[str] = ()) -> str:
    """The suggested slug, normalized and suffixed (-2, -3, ...) until not in ``taken``."""
    slug = f"proposal-{proposal.id}"
    for candidate in (proposal.slug_suggestion, proposal.label_en):
        normalized = re.sub(r"[^a-z0-9]+", "-", (candidate or "").lower()).strip("-")[:64].rstrip("-")
        if SLUG_PATTERN.fullmatch(normalized):
            slug = normalized
            break
    taken = set(taken)
    unique, suffix = slug, 2
    while unique in taken:
        tail = f"-{suffix}"
        unique = slug[:64 - len(tail)].rstrip("-") + tail
        suffix += 1
    return unique


async def _taken_theme_slugs(db: AsyncSession) -> set[str]:
    """Slugs a new taxonomy entry must avoid: every ``themes`` row (retired included,
    which a duplicate would silently reactivate) and every entry of the taxonomy file
    (a duplicate there makes the whole file fail validation)."""
    taken = set((await db.execute(select(Theme.slug))).scalars())
    try:
        taken.update(theme.slug for theme in load_taxonomy().themes)
    except (OSError, ValueError, yaml.YAMLError):
        pass  # an unreadable file already breaks the taxonomy sync; the DB slugs still apply
    return taken


def _taxonomy_ticker(ticker: str) -> str:
    # The taxonomy file lists bare US symbols; the loader canonicalizes them back.
    return ticker[:-3] if ticker.endswith(".US") else ticker


def proposal_yaml_snippet(proposal: ThemeProposal, taken: Iterable[str] = ()) -> str:
    """A themes_v1.yaml entry (indented under ``themes:``) for an accepted proposal.

    The slug avoids ``taken`` (see ``_taken_theme_slugs``) so the entry can be pasted as is.
    """
    slug = _proposal_slug(proposal, taken)
    name_en = " ".join((proposal.label_en or "").split()) or slug
    entry = {
        "slug": slug,
        "name_en": name_en,
        "name_zh": " ".join((proposal.label_zh or "").split()) or name_en,
        "megatrend": None,
        "definition": " ".join((proposal.rationale or "").split()) or f"TODO: define what is in and out of {slug}.",
        "keywords": [],
        "negative_keywords": [],
        "proxy_etfs": [],
        "seed_core": [_taxonomy_ticker(ticker) for ticker in _string_list(proposal.tickers)],
        "pins": [],
        "bans": [],
        "status": "active",
    }
    text = yaml.safe_dump([entry], allow_unicode=True, sort_keys=False, default_flow_style=None, width=88)
    return textwrap.indent(text, "  ")


async def list_theme_proposals(db: AsyncSession, *, status: Optional[str] = None) -> dict:
    query = select(ThemeProposal)
    if status is not None:
        query = query.where(ThemeProposal.status == status)
    query = query.order_by(
        case((ThemeProposal.status == "pending", 0), else_=1),
        ThemeProposal.last_seen.desc(),
        ThemeProposal.id.desc(),
    )
    proposals = (await db.execute(query)).scalars()
    return {"proposals": [_proposal_item(proposal) for proposal in proposals]}


async def decide_theme_proposal(db: AsyncSession, proposal_id: int, decision: str) -> dict:
    if decision not in ("accepted", "rejected"):
        raise ValueError(f"Unsupported proposal decision: {decision}")
    proposal = await db.get(ThemeProposal, proposal_id)
    if proposal is None:
        raise ThemeProposalNotFound(f"Theme proposal {proposal_id} not found")
    proposal.status = decision
    proposal.decided_at = utc_now()
    await db.commit()
    await db.refresh(proposal)
    snippet = None
    if decision == "accepted":
        snippet = proposal_yaml_snippet(proposal, await _taken_theme_slugs(db))
    return {"proposal": _proposal_item(proposal), "yaml_snippet": snippet}
