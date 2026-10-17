"""Daily market-wide EODHD news ingestion -> per-ticker attention counts.

Each UTC calendar day of the general feed is requested with ``from=to=day`` and
paged 1,000 articles at a time (5 API calls per page). A day is ``complete``
only after the whole UTC day has passed and every page arrived; partial and
failed days are never read as zero attention (see ``load_attention``). A day
counts only articles from its own request, so its numbers never depend on
which other days the same run fetched.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import logging
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx
import numpy as np
import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.time_utils import utc_now
from database import async_session_maker
from models import NewsIngestDay, NewsItem, TickerAttentionDaily
from services.data_quality import QualityReport
from services.eodhd_client import EODHDQuotaExceeded, get_news
from services.news_fetcher import _safe_article_link
from services.pipeline_runs import (
    begin_pipeline_run,
    finish_pipeline_run,
    publish_datasets_and_finish,
    update_pipeline_run,
)
from services.raw_store import persist_snapshot

logger = logging.getLogger(__name__)

NEWS_ATTENTION_DATASET = "news_attention"
NEWS_INGEST_VERSION = "news-attention-v1"
NEWS_RAW_DATASET = "news_market_feed"
NEWS_TRIM_VERSION = "news-trim-v1"
PAGE_SIZE = 1000
CALLS_PER_PAGE = 5
WINDOW_DAYS = 7
WRITE_CHUNK = 500
MAX_CONSECUTIVE_FAILED_DAYS = 2  # then the provider is down: skip the rest instead of retrying every day
REFETCH_MIN_RETAINED = 0.5  # a complete day refetched with fewer articles than this share keeps its data
OFF_DAY_WARN_SHARE = 0.05  # more ignored off-day articles than this suggests the feed is not UTC-aligned


@dataclass
class AttentionData:
    counts: pd.DataFrame  # UTC days x tickers (complete days zero-filled; incomplete days NaN rows)
    totals: pd.Series  # UTC day -> total US-linked deduplicated articles (NaN if incomplete)
    complete_days: set[date]


@dataclass(frozen=True)
class _Article:
    url_hash: str
    day: date
    published_at: datetime
    title: str
    title_key: str
    link: Optional[str]
    source_domain: Optional[str]
    tickers: tuple[str, ...]
    tags: list[str]


@dataclass
class _DayFetch:
    day: date
    status: str = "failed"  # complete | partial (truncated) | failed (a page was unavailable)
    pages: int = 0
    truncated: bool = False
    raw_rows: list[dict[str, Any]] = field(default_factory=list)
    articles: list[_Article] = field(default_factory=list)


@dataclass
class _RunStats:
    recent_days: list[date] = field(default_factory=list)
    backfill_days: list[date] = field(default_factory=list)
    requests: int = 0
    rows_fetched: int = 0
    rows_undated: int = 0
    articles_off_day_ignored: int = 0
    complete: list[date] = field(default_factory=list)
    partial: list[date] = field(default_factory=list)
    failed: list[date] = field(default_factory=list)
    kept_previous: list[date] = field(default_factory=list)
    refetch_shrunk: list[date] = field(default_factory=list)
    errored: list[date] = field(default_factory=list)
    skipped: list[date] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    articles_us: int = 0
    attention_rows: int = 0
    news_items_upserted: int = 0
    news_items_deleted: int = 0
    quota_exhausted: bool = False

    @property
    def days_fetched(self) -> int:
        return (len(self.complete) + len(self.partial) + len(self.failed) + len(self.kept_previous)
                + len(self.refetch_shrunk) + len(self.errored))

    @property
    def days_fetched_ok(self) -> int:
        return len(self.complete) + len(self.partial) + len(self.refetch_shrunk)


def _create_news_client() -> httpx.AsyncClient:
    """One pool for the whole run; trust_env stays on like ``eodhd_client.create_http_client``.

    The timeout is longer than the client default because a page carries 1,000
    full-body articles.
    """
    return httpx.AsyncClient(timeout=settings.THEME_NEWS_TIMEOUT_SECONDS)


def _safe_error(exc: BaseException) -> str:
    # str() of an httpx error embeds the request URL, which carries the api_token.
    if isinstance(exc, httpx.HTTPError):
        return type(exc).__name__
    return f"{type(exc).__name__}: {exc}"[:500]


def _parse_published_at(value: Any) -> Optional[datetime]:
    """Naive UTC timestamp from the feed's ISO ``date``; offset-less values are UTC.

    Unparseable dates and offsets that leave the datetime range read as undated.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, OverflowError):
        return None
    return parsed


def _text(value: Any) -> str:
    """Stripped text that encodes as UTF-8 (JSON can carry lone surrogates, which SQLite rejects)."""
    text = str(value or "").strip()
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        text = text.encode("utf-8", "replace").decode("utf-8")
    return text


def _article_link(value: Any) -> str:
    """``news_fetcher._safe_article_link``; a link that does not parse counts as no link."""
    try:
        # urlparse runs the same urlsplit checks that _url_hash/_source_domain run later.
        return _safe_article_link(_text(value))
    except ValueError:
        return ""


def _us_tickers(symbols: list[str]) -> tuple[str, ...]:
    """Canonical US listings (``BRK-B.US``); foreign cross-listings are dropped."""
    found = set()
    for symbol in symbols:
        value = symbol.strip().upper()
        if not value.endswith(".US"):
            continue
        base = value[:-3].replace(".", "-").replace("/", "-")
        if base:
            found.add(f"{base}.US")
    return tuple(sorted(found))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for text in (_text(item) for item in value if isinstance(item, str)) if text]


def _url_hash(link: str, published_at: datetime, title_key: str, tickers: tuple[str, ...]) -> str:
    if link:
        parts = urlsplit(link)
        identity = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", ""))
    else:
        # Without a trustworthy link the article's own fields are its identity.
        identity = "\0".join(("nolink", published_at.isoformat(), title_key, ",".join(tickers)))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _source_domain(link: str) -> Optional[str]:
    host = urlsplit(link).hostname if link else None
    if not host:
        return None
    return host[4:] if host.startswith("www.") else host


def _consume_page(page: list[dict[str, Any]], fetch: _DayFetch, stats: _RunStats) -> None:
    for row in page:
        stats.rows_fetched += 1
        raw_date = row.get("date")
        raw_title = _text(row.get("title"))
        # Provider titles carry HTML entities ("AI &amp; Data Centers"); store readable text.
        title = " ".join(html.unescape(raw_title).split())
        link = _article_link(row.get("link"))
        symbols = _string_list(row.get("symbols"))
        tags = _string_list(row.get("tags"))
        # Lineage keeps what the provider sent for this request, minus article bodies.
        fetch.raw_rows.append({
            "date": raw_date if isinstance(raw_date, str) else None,
            "title": raw_title,
            "link": link,
            "symbols": symbols,
            "tags": tags,
        })
        published_at = _parse_published_at(raw_date)
        if published_at is None:
            stats.rows_undated += 1
            continue
        tickers = _us_tickers(symbols)
        title_key = " ".join(title.lower().split())
        fetch.articles.append(_Article(
            url_hash=_url_hash(link, published_at, title_key, tickers),
            day=published_at.date(),
            published_at=published_at,
            title=title,
            title_key=title_key,
            link=link or None,
            source_domain=_source_domain(link),
            tickers=tickers,
            tags=tags,
        ))


async def _fetch_day(day: date, client: httpx.AsyncClient, stats: _RunStats) -> _DayFetch:
    fetch = _DayFetch(day=day)
    day_text = day.isoformat()
    for page_index in range(settings.THEME_NEWS_MAX_PAGES_PER_DAY):
        offset = page_index * PAGE_SIZE
        stats.requests += 1
        page = await get_news(
            from_date=day_text, to_date=day_text, limit=PAGE_SIZE, offset=offset, client=client,
        )
        if page is None:
            logger.warning("EODHD market news page unavailable for %s at offset %s; day marked failed",
                           day_text, offset)
            return _DayFetch(day=day, status="failed", pages=fetch.pages)
        fetch.pages += 1
        _consume_page(page, fetch, stats)
        if len(page) < PAGE_SIZE:
            fetch.status = "complete"
            return fetch
    logger.warning("EODHD market news for %s exceeded %s pages; day marked partial",
                   day_text, settings.THEME_NEWS_MAX_PAGES_PER_DAY)
    fetch.status = "partial"
    fetch.truncated = True
    return fetch


def _dedupe(articles: list[_Article]) -> list[_Article]:
    """Drop repeated normalized links, then repeated normalized titles, keeping the earliest.

    A dropped copy's US tickers are merged into the kept one, so a ticker never
    loses the article because the earliest copy was tagged differently.
    """
    kept: list[_Article] = []
    by_link: dict[str, int] = {}
    by_title: dict[str, int] = {}
    for article in sorted(articles, key=lambda item: (item.published_at, item.url_hash, item.title)):
        position = by_link.get(article.url_hash)
        if position is None and article.title_key:
            position = by_title.get(article.title_key)
        if position is None:
            position = len(kept)
            kept.append(article)
            if article.title_key:
                by_title[article.title_key] = position
        elif not set(article.tickers) <= set(kept[position].tickers):
            merged = tuple(sorted({*kept[position].tickers, *article.tickers}))
            kept[position] = replace(kept[position], tickers=merged)
        by_link.setdefault(article.url_hash, position)
    return kept


async def _claimed_elsewhere(db: AsyncSession, articles: list[_Article], day: date) -> set[str]:
    """Links whose saved ``news_items`` row already counts them on another UTC day."""
    hashes = sorted({article.url_hash for article in articles})
    claimed: set[str] = set()
    for start in range(0, len(hashes), WRITE_CHUNK):
        claimed.update((await db.execute(select(NewsItem.url_hash).where(
            NewsItem.url_hash.in_(hashes[start:start + WRITE_CHUNK]), NewsItem.day != day,
        ))).scalars())
    return claimed


def _attention_rows(articles: list[_Article], day: date, run_id: int) -> list[dict[str, Any]]:
    focused_max = settings.THEME_NEWS_FOCUSED_MAX_SYMBOLS
    counts: Counter[str] = Counter()
    focused: Counter[str] = Counter()
    for article in articles:
        counts.update(article.tickers)
        if len(article.tickers) <= focused_max:
            focused.update(article.tickers)
    return [
        {"ticker": ticker, "day": day, "article_count": count, "focused_count": focused.get(ticker, 0),
         "source_run_id": run_id}
        for ticker, count in sorted(counts.items())
    ]


async def _upsert_ingest_day(db: AsyncSession, values: dict[str, Any]) -> None:
    stmt = insert(NewsIngestDay).values(**values)
    await db.execute(stmt.on_conflict_do_update(
        index_elements=["day"],
        set_={key: getattr(stmt.excluded, key) for key in values if key != "day"},
    ))


async def _write_day(
    fetch: _DayFetch, articles: list[_Article], run_id: int, today: date, claimed: set[str], stats: _RunStats,
) -> None:
    """Replace one UTC day's counts (and saved headlines) in a single short write transaction.

    A link is counted on one UTC day only: the first day written with it.
    ``claimed`` holds the links this run already wrote; saved ``news_items``
    carry the claim across runs within the retention window.
    """
    day = fetch.day
    keep_news = day >= today - timedelta(days=settings.THEME_NEWS_RETENTION_DAYS)
    now = utc_now()
    async with async_session_maker() as db, db.begin():
        previous = (await db.execute(
            select(NewsIngestDay.status, NewsIngestDay.articles_total).where(NewsIngestDay.day == day)
        )).first()
        was_complete = previous is not None and previous.status == "complete"
        if fetch.status != "complete" and was_complete:
            # A failed or truncated refetch never replaces a day that was complete.
            stats.kept_previous.append(day)
            return
        kept: list[_Article] = []
        if fetch.status != "failed":
            elsewhere = await _claimed_elsewhere(db, articles, day)
            kept = _dedupe([article for article in articles
                            if article.url_hash not in claimed and article.url_hash not in elsewhere])
        if was_complete and len(kept) < (previous.articles_total or 0) * REFETCH_MIN_RETAINED:
            # An emptied or mostly emptied refetch of a complete day is a provider gap, not news.
            logger.warning("EODHD market news for %s returned %s articles after %s; kept the earlier data",
                           day, len(kept), previous.articles_total)
            stats.refetch_shrunk.append(day)
            return
        attention_rows = _attention_rows(kept, day, run_id)
        articles_us = sum(1 for article in kept if article.tickers)
        snapshot_id = None
        if fetch.status != "failed":
            raw_rows = sorted(fetch.raw_rows, key=lambda row: (row["date"] or "", row["link"], row["title"]))
            snapshot = await persist_snapshot(
                db, "EODHD", NEWS_RAW_DATASET, raw_rows, as_of_date=day,
                details={"from": day.isoformat(), "to": day.isoformat(), "limit": PAGE_SIZE,
                         "pages": fetch.pages, "truncated": fetch.truncated, "trim": NEWS_TRIM_VERSION},
            )
            snapshot_id = snapshot.id
        await db.execute(delete(TickerAttentionDaily).where(TickerAttentionDaily.day == day))
        for start in range(0, len(attention_rows), WRITE_CHUNK):
            await db.execute(insert(TickerAttentionDaily), attention_rows[start:start + WRITE_CHUNK])
        news_rows = [
            {"url_hash": article.url_hash, "day": article.day, "published_at": article.published_at,
             "title": article.title, "link": article.link, "source_domain": article.source_domain,
             "tickers": list(article.tickers), "symbol_count": len(article.tickers), "tags": article.tags,
             "raw_snapshot_id": snapshot_id, "ingested_at": now}
            for article in kept
        ] if keep_news else []
        if keep_news and fetch.status != "failed":
            # Saved headlines mirror the day's counted articles, like the counts themselves.
            await db.execute(delete(NewsItem).where(NewsItem.day == day))
        for start in range(0, len(news_rows), WRITE_CHUNK):
            stmt = insert(NewsItem)
            await db.execute(stmt.on_conflict_do_update(
                index_elements=["url_hash"],
                set_={key: getattr(stmt.excluded, key) for key in news_rows[0] if key != "url_hash"},
            ), news_rows[start:start + WRITE_CHUNK])
        await _upsert_ingest_day(db, {
            "day": day, "status": fetch.status, "pages": fetch.pages, "articles_total": len(kept),
            "articles_us": articles_us, "truncated": fetch.truncated, "raw_snapshot_id": snapshot_id,
            "source_run_id": run_id, "ingested_at": now,
        })
    claimed.update(article.url_hash for article in kept)
    getattr(stats, fetch.status).append(day)
    stats.articles_us += articles_us
    stats.attention_rows += len(attention_rows)
    stats.news_items_upserted += len(news_rows)


def _recent_bounds(target: date, today: date) -> tuple[date, date]:
    """(newest day to fetch, newest backfill candidate); today's UTC day is never complete."""
    recent_end = min(target + timedelta(days=1), today - timedelta(days=1))
    return recent_end, recent_end - timedelta(days=settings.THEME_NEWS_RECENT_REFRESH_DAYS)


async def _plan_days(db: AsyncSession, target: date, today: date, backfill_limit: int) -> tuple[list[date], list[date]]:
    """Recent days (always refetched) and the newest missing older days, both newest first.

    Missing days of the target's attention window behind the recent refresh are
    always planned; ``backfill_limit`` caps only the older history. Those days
    fall between two recent ranges whenever a run is skipped (the Tuesday run
    after a Monday holiday has an already-published target) or the worker was
    down for more than the refresh span, and nothing else fetches them.
    """
    start = settings.THEME_NEWS_BACKFILL_START
    recent_end, older_end = _recent_bounds(target, today)
    recent = [recent_end - timedelta(days=offset) for offset in range((recent_end - older_end).days)]
    recent = [day for day in recent if day >= start]
    if older_end < start:
        return recent, []
    window_start = target - timedelta(days=WINDOW_DAYS - 1)
    complete = set((await db.execute(
        select(NewsIngestDay.day).where(
            NewsIngestDay.status == "complete", NewsIngestDay.day >= start, NewsIngestDay.day <= older_end,
        )
    )).scalars())
    missing = []
    older = 0
    day = older_end
    while day >= start and (day >= window_start or older < backfill_limit):
        if day not in complete:
            missing.append(day)
            if day < window_start:
                older += 1
        day -= timedelta(days=1)
    return recent, missing


async def _ingest_days(days: list[date], client: httpx.AsyncClient, run_id: int, today: date, stats: _RunStats) -> None:
    """Fetch and write the planned UTC days newest first, one transaction per day.

    A day counts only the articles of its own request whose UTC date is that
    day; articles stamped on another day are ignored, so a day's numbers never
    depend on which neighbouring days the run also fetched. An unexpected error
    fails only its day. After MAX_CONSECUTIVE_FAILED_DAYS unavailable days in a
    row the rest are skipped; they stay incomplete and the next run plans them.
    """
    claimed: set[str] = set()
    failures = 0
    for position, day in enumerate(days):
        if failures >= MAX_CONSECUTIVE_FAILED_DAYS:
            stats.skipped = days[position:]
            logger.warning("EODHD market news unavailable for %s days in a row; skipped the remaining %s days",
                           failures, len(stats.skipped))
            return
        try:
            fetch = await _fetch_day(day, client, stats)
        except EODHDQuotaExceeded:
            stats.quota_exhausted = True
            raise
        except Exception as exc:
            # Only the type: an EODHD call's error text can carry the request URL.
            logger.error("EODHD market news for %s raised %s; day marked failed", day, type(exc).__name__)
            stats.errors.append(f"{day.isoformat()} fetch: {type(exc).__name__}")
            fetch = _DayFetch(day=day, status="failed")
        on_day = [article for article in fetch.articles if article.day == day]
        stats.articles_off_day_ignored += len(fetch.articles) - len(on_day)
        try:
            await _write_day(fetch, on_day, run_id, today, claimed, stats)
        except Exception as exc:
            logger.error("Writing market news for %s failed: %s", day, _safe_error(exc))
            stats.errored.append(day)
            stats.errors.append(f"{day.isoformat()} write: {_safe_error(exc)}"[:300])
            failures += 1
            continue
        failures = failures + 1 if fetch.status == "failed" else 0
        await update_pipeline_run(run_id, "ingesting_news", records_processed=stats.days_fetched)


def _isoformat(days: Iterable[date], limit: int = 30) -> list[str]:
    return [day.isoformat() for day in sorted(days)][:limit]


def _quality_report(
    stats: _RunStats,
    target: date,
    *,
    window_missing: Optional[list[date]] = None,
    backfill_remaining: Optional[int] = None,
) -> QualityReport:
    published = window_missing is not None and not window_missing
    metrics = {
        "as_of_date": target.isoformat(),
        "window_start": (target - timedelta(days=WINDOW_DAYS - 1)).isoformat(),
        "window_complete": published,
        "window_missing": None if window_missing is None else _isoformat(window_missing),
        "recent_days": _isoformat(stats.recent_days),
        "backfill_days": len(stats.backfill_days),
        "backfill_remaining": backfill_remaining,
        "days_fetched": stats.days_fetched,
        "days_complete": len(stats.complete),
        "days_partial": len(stats.partial),
        "days_failed": len(stats.failed),
        "days_kept_previous": len(stats.kept_previous),
        "days_refetch_shrunk": len(stats.refetch_shrunk),
        "days_errored": len(stats.errored),
        "days_skipped": len(stats.skipped),
        "requests": stats.requests,
        "calls_estimate": stats.requests * CALLS_PER_PAGE,
        "rows_fetched": stats.rows_fetched,
        "rows_undated": stats.rows_undated,
        "articles_us": stats.articles_us,
        "articles_off_day_ignored": stats.articles_off_day_ignored,
        "attention_rows": stats.attention_rows,
        "news_items_upserted": stats.news_items_upserted,
        "news_items_deleted": stats.news_items_deleted,
        "quota_exhausted": stats.quota_exhausted,
    }
    errors, warnings = [], []
    if window_missing:
        errors.append("News days not complete for the attention window: " + ", ".join(_isoformat(window_missing)))
    if stats.failed:
        warnings.append("News feed unavailable for: " + ", ".join(_isoformat(stats.failed)))
    if stats.partial:
        warnings.append("News feed truncated at the page limit for: " + ", ".join(_isoformat(stats.partial)))
    if stats.kept_previous:
        warnings.append("Refetch failed; kept earlier complete data for: " + ", ".join(_isoformat(stats.kept_previous)))
    if stats.refetch_shrunk:
        warnings.append("Refetch returned far fewer articles; kept earlier complete data for: "
                        + ", ".join(_isoformat(stats.refetch_shrunk)))
    if stats.errors:
        warnings.append("Unexpected ingestion errors: " + "; ".join(stats.errors[:5]))
    if stats.skipped:
        skipped = _isoformat(stats.skipped, limit=len(stats.skipped))
        warnings.append(f"News feed unavailable for {MAX_CONSECUTIVE_FAILED_DAYS} days in a row; "
                        f"skipped {len(skipped)} days ({skipped[0]} to {skipped[-1]})")
    dated = stats.rows_fetched - stats.rows_undated
    if dated and stats.articles_off_day_ignored > OFF_DAY_WARN_SHARE * dated:
        warnings.append(f"{stats.articles_off_day_ignored} of {dated} dated articles fell outside their "
                        "requested UTC day and were ignored")
    if stats.quota_exhausted:
        errors.append("EODHD daily API call limit is exhausted")
    return QualityReport(passed=published, metrics=metrics, errors=errors, warnings=warnings)


async def ingest_market_news(target: date, *, max_backfill_days: Optional[int] = None) -> dict:
    """Refresh recent UTC news days, backfill older gaps, and publish ``news_attention``.

    ``news_attention`` is published for ``target`` once every UTC day from
    target-6 through target is complete. Raises ``EODHDQuotaExceeded`` after
    recording the run as failed; days written before the quota ran out are kept.
    """
    today = utc_now().date()
    backfill_limit = (settings.THEME_NEWS_BACKFILL_DAYS_PER_RUN if max_backfill_days is None
                      else max(0, max_backfill_days))
    async with async_session_maker() as db:
        recent, backfill = await _plan_days(db, target, today, backfill_limit)
    stats = _RunStats(recent_days=recent, backfill_days=backfill)
    run_id = await begin_pipeline_run("news_attention", target, version=NEWS_INGEST_VERSION)
    try:
        await update_pipeline_run(run_id, "fetching_news")
        async with _create_news_client() as client:
            await _ingest_days(recent + backfill, client, run_id, today, stats)

        window = [target - timedelta(days=offset) for offset in range(WINDOW_DAYS - 1, -1, -1)]
        start = settings.THEME_NEWS_BACKFILL_START
        _, older_end = _recent_bounds(target, today)
        async with async_session_maker() as db, db.begin():
            deleted = await db.execute(delete(NewsItem).where(
                NewsItem.day < today - timedelta(days=settings.THEME_NEWS_RETENTION_DAYS)))
            stats.news_items_deleted = max(deleted.rowcount or 0, 0)
            complete = set((await db.execute(select(NewsIngestDay.day).where(
                NewsIngestDay.status == "complete", NewsIngestDay.day.in_(window),
            ))).scalars())
            missing = [day for day in window if day not in complete]
            backfill_remaining = 0
            if older_end >= start:
                complete_older = await db.scalar(select(func.count()).select_from(NewsIngestDay).where(
                    NewsIngestDay.status == "complete", NewsIngestDay.day >= start, NewsIngestDay.day <= older_end,
                ))
                backfill_remaining = (older_end - start).days + 1 - int(complete_older or 0)
            report = _quality_report(stats, target, window_missing=missing, backfill_remaining=backfill_remaining)
            if not missing:
                await publish_datasets_and_finish(
                    db, [NEWS_ATTENTION_DATASET], target, run_id,
                    quality_report=report.to_dict(), records_processed=stats.days_fetched,
                )
        if missing:
            # No requested day arrived: the provider was unavailable, not merely behind.
            status = "failed" if stats.days_fetched and not stats.days_fetched_ok else "partial"
            await finish_pipeline_run(run_id, status, quality_report=report.to_dict(),
                                      error_message="; ".join(report.errors) or None)
        else:
            status = "published"
        logger.info(
            "News attention %s for %s: %s days fetched (%s complete, %s partial, %s failed), ~%s API calls",
            status, target, stats.days_fetched, len(stats.complete), len(stats.partial), len(stats.failed),
            stats.requests * CALLS_PER_PAGE,
        )
        return {"status": status, "run_id": run_id, "published": status == "published", **report.metrics}
    except asyncio.CancelledError:
        await finish_pipeline_run(run_id, "cancelled", quality_report=_quality_report(stats, target).to_dict(),
                                  error_message="News ingestion was cancelled")
        raise
    except EODHDQuotaExceeded:
        logger.error("EODHD quota exhausted during news ingestion for %s; stopped after %s days",
                     target, stats.days_fetched)
        await finish_pipeline_run(run_id, "failed", quality_report=_quality_report(stats, target).to_dict(),
                                  error_message="EODHD daily API call limit is exhausted")
        raise
    except Exception as exc:
        logger.error("News ingestion failed for %s: %s", target, _safe_error(exc))
        await finish_pipeline_run(run_id, "failed", quality_report=_quality_report(stats, target).to_dict(),
                                  error_message=_safe_error(exc))
        raise


async def load_attention(
    db: AsyncSession, start_day: date, end_day: date, tickers: Optional[Iterable[str]] = None
) -> AttentionData:
    """Per-ticker article counts for every UTC day in [start_day, end_day].

    Complete days are zero-filled (absent rows mean no articles); every other
    day is an all-NaN row, and so is its total. Without ``tickers`` the columns
    are the tickers seen on complete days in the range.
    """
    span = (end_day - start_day).days + 1
    days = [start_day + timedelta(days=offset) for offset in range(max(span, 0))]
    index = pd.Index(days, dtype=object, name="date")
    totals_by_day = {
        day: articles_us
        for day, articles_us in (await db.execute(
            select(NewsIngestDay.day, NewsIngestDay.articles_us).where(
                NewsIngestDay.status == "complete", NewsIngestDay.day >= start_day, NewsIngestDay.day <= end_day,
            )
        )).all()
    }
    complete_days = set(totals_by_day)
    requested = None if tickers is None else list(dict.fromkeys(str(ticker) for ticker in tickers))

    observations: list[tuple[str, date, int]] = []
    if complete_days and requested != []:
        base = select(TickerAttentionDaily.ticker, TickerAttentionDaily.day, TickerAttentionDaily.article_count).where(
            TickerAttentionDaily.day >= start_day, TickerAttentionDaily.day <= end_day,
        )
        chunks = [None] if requested is None else [
            requested[start:start + WRITE_CHUNK] for start in range(0, len(requested), WRITE_CHUNK)
        ]
        for chunk in chunks:
            stmt = base if chunk is None else base.where(TickerAttentionDaily.ticker.in_(chunk))
            observations.extend(row for row in (await db.execute(stmt)).all() if row[1] in complete_days)

    columns = requested if requested is not None else sorted({ticker for ticker, _, _ in observations})
    values = np.full((len(days), len(columns)), np.nan)
    row_of = {day: position for position, day in enumerate(days)}
    values[[row_of[day] for day in complete_days], :] = 0.0
    if observations:
        column_of = {ticker: position for position, ticker in enumerate(columns)}
        rows = [row_of[day] for _, day, _ in observations]
        cols = [column_of[ticker] for ticker, _, _ in observations]
        values[rows, cols] = [float(count) for _, _, count in observations]
    counts = pd.DataFrame(values, index=index, columns=pd.Index(columns, dtype=object))
    totals = pd.Series(
        [float(totals_by_day[day]) if day in totals_by_day else np.nan for day in days], index=index, dtype=float,
    )
    return AttentionData(counts=counts, totals=totals, complete_days=complete_days)
