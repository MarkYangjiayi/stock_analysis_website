import gzip
import json
import logging
import math
from datetime import date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from core.config import settings
from models import DataPublication, NewsIngestDay, NewsItem, PipelineRun, RawDataSnapshot, TickerAttentionDaily
from services import eodhd_client
from services.eodhd_client import EODHDQuotaExceeded
from services.pipeline_runs import latest_published_date
from services.themes import news_ingest
from services.themes.news_ingest import NEWS_ATTENTION_DATASET, ingest_market_news, load_attention


NOW = datetime(2026, 10, 10, 9, 0)  # Saturday 05:00 ET; UTC 10-09 has fully passed.
TARGET = date(2026, 10, 9)
WINDOW = [TARGET - timedelta(days=offset) for offset in range(6, -1, -1)]


def article(timestamp, title, link, symbols=("NVDA.US",), tags=("AI",)):
    return {
        "date": timestamp,
        "title": title,
        "link": link,
        "symbols": list(symbols),
        "tags": list(tags),
        "content": "Full article body. " * 40,
        "sentiment": {"polarity": 0.1},
    }


def full_page(day, page, size=1000, symbols=("NVDA.US",)):
    return [
        article(f"{day}T12:00:00+00:00", f"{day} story {page}-{index}", f"https://news.example/{day}/{page}/{index}",
                symbols)
        for index in range(size)
    ]


class FakeFeed:
    """Market-wide /api/news keyed by (day, offset); unknown pages are empty days."""

    def __init__(self):
        self.pages = {}
        self.default = []
        self.calls = []

    async def __call__(self, *, ticker=None, tag=None, from_date=None, to_date=None, limit=50, offset=0, client=None):
        assert ticker is None and tag is None
        assert from_date == to_date and limit == 1000 and client is not None
        self.calls.append((from_date, offset))
        value = self.pages.get((from_date, offset), self.default)
        if isinstance(value, Exception):
            raise value
        return value

    def days_called(self):
        return list(dict.fromkeys(day for day, _ in self.calls))


def _refuse_network(request):
    raise AssertionError(f"unexpected network request to {request.url.host}")


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(news_ingest, "utc_now", lambda: NOW)
    monkeypatch.setattr(settings, "THEME_NEWS_BACKFILL_START", WINDOW[0])
    monkeypatch.setattr(settings, "THEME_NEWS_RECENT_REFRESH_DAYS", 3)
    monkeypatch.setattr(settings, "THEME_NEWS_MAX_PAGES_PER_DAY", 20)
    monkeypatch.setattr(settings, "THEME_NEWS_RETENTION_DAYS", 45)
    monkeypatch.setattr(settings, "THEME_NEWS_FOCUSED_MAX_SYMBOLS", 5)
    monkeypatch.setattr(settings, "THEME_NEWS_BACKFILL_DAYS_PER_RUN", 120)


@pytest.fixture
def feed(monkeypatch, clock):
    fake = FakeFeed()
    monkeypatch.setattr(news_ingest, "get_news", fake)
    monkeypatch.setattr(
        news_ingest, "_create_news_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(_refuse_network)),
    )
    return fake


async def day_counts(db, day):
    rows = (await db.execute(select(TickerAttentionDaily).where(TickerAttentionDaily.day == day))).scalars()
    return {row.ticker: (row.article_count, row.focused_count) for row in rows}


async def ingest_day(db, day):
    return await db.scalar(select(NewsIngestDay).where(NewsIngestDay.day == day))


async def test_pages_each_day_until_a_short_page(feed, db_session):
    feed.pages[("2026-10-09", 0)] = full_page("2026-10-09", 0)
    feed.pages[("2026-10-09", 1000)] = full_page("2026-10-09", 1)
    feed.pages[("2026-10-09", 2000)] = full_page("2026-10-09", 2, size=3)

    result = await ingest_market_news(TARGET, max_backfill_days=0)

    assert [call for call in feed.calls if call[0] == "2026-10-09"] == [
        ("2026-10-09", 0), ("2026-10-09", 1000), ("2026-10-09", 2000),
    ]
    # Recent days, then the window's missing days behind them (fetched even without a backfill budget).
    assert feed.days_called() == [day.isoformat() for day in reversed(WINDOW)]
    assert result["days_fetched"] == 7 and result["days_complete"] == 7
    assert result["calls_estimate"] == 5 * (3 + 6)
    day = await ingest_day(db_session, TARGET)
    assert (day.status, day.pages, day.truncated, day.articles_total, day.articles_us) == ("complete", 3, False, 2003, 2003)
    assert await day_counts(db_session, TARGET) == {"NVDA.US": (2003, 2003)}


async def test_page_limit_truncates_day_as_partial(feed, monkeypatch, db_session):
    monkeypatch.setattr(settings, "THEME_NEWS_MAX_PAGES_PER_DAY", 2)
    feed.pages[("2026-10-09", 0)] = full_page("2026-10-09", 0)
    feed.pages[("2026-10-09", 1000)] = full_page("2026-10-09", 1)
    feed.pages[("2026-10-09", 2000)] = full_page("2026-10-09", 2)

    result = await ingest_market_news(TARGET)

    assert [offset for day, offset in feed.calls if day == "2026-10-09"] == [0, 1000]
    day = await ingest_day(db_session, TARGET)
    assert (day.status, day.truncated, day.pages) == ("partial", True, 2)
    assert result["status"] == "partial" and result["days_partial"] == 1
    assert result["window_missing"] == ["2026-10-09"]
    assert await latest_published_date(NEWS_ATTENTION_DATASET) is None
    attention = await load_attention(db_session, TARGET, TARGET)
    assert attention.counts.isna().all().all() and math.isnan(attention.totals[TARGET])


async def test_unavailable_page_fails_the_day_and_blocks_publication(feed, db_session):
    feed.pages[("2026-10-05", 0)] = full_page("2026-10-05", 0)
    feed.pages[("2026-10-05", 1000)] = None

    result = await ingest_market_news(TARGET)

    assert result["status"] == "partial"
    assert result["days_failed"] == 1 and result["window_missing"] == ["2026-10-05"]
    failed = await ingest_day(db_session, date(2026, 10, 5))
    assert (failed.status, failed.articles_total, failed.raw_snapshot_id) == ("failed", 0, None)
    assert await day_counts(db_session, date(2026, 10, 5)) == {}
    assert (await db_session.execute(select(DataPublication))).first() is None
    run = await db_session.get(PipelineRun, result["run_id"])
    assert run.status == "partial" and "2026-10-05" in run.error_message
    assert run.quality_report["metrics"]["days_failed"] == 1


async def test_failed_refetch_keeps_previous_complete_day(feed, db_session):
    feed.pages[("2026-10-08", 0)] = [article("2026-10-08T14:00:00Z", "Chips rally", "https://a.example/1")]
    first = await ingest_market_news(TARGET)
    assert first["status"] == "published"

    feed.pages[("2026-10-08", 0)] = None
    second = await ingest_market_news(TARGET)

    assert second["status"] == "published" and second["days_kept_previous"] == 1
    day = await ingest_day(db_session, date(2026, 10, 8))
    assert day.status == "complete" and day.source_run_id == first["run_id"]
    assert await day_counts(db_session, date(2026, 10, 8)) == {"NVDA.US": (1, 1)}


async def test_dedupes_by_normalized_link_then_title_within_a_day(feed, db_session):
    feed.pages[("2026-10-09", 0)] = [
        article("2026-10-09T13:00:00Z", "Chip rally", "https://News.Example.com/a?utm=1#top"),
        article("2026-10-09T13:05:00Z", "Chip rally, updated", "https://news.example.com/a?utm=2"),
        article("2026-10-09T13:10:00Z", "  chip   RALLY ", "https://other.example/b"),
        article("2026-10-09T14:00:00Z", "Memory demand", "https://news.example.com/c",
                symbols=("NVDA.US", "MU.US", "1NVDA.MI")),
        article("2026-10-09T15:00:00Z", "No link here", "javascript:alert(1)", symbols=("AMD.US",)),
    ]
    feed.pages[("2026-10-08", 0)] = [article("2026-10-08T13:00:00Z", "Chip rally", "https://other.example/x")]

    await ingest_market_news(TARGET, max_backfill_days=0)

    assert await day_counts(db_session, TARGET) == {"NVDA.US": (2, 2), "MU.US": (1, 1), "AMD.US": (1, 1)}
    assert await day_counts(db_session, date(2026, 10, 8)) == {"NVDA.US": (1, 1)}
    day = await ingest_day(db_session, TARGET)
    assert (day.articles_total, day.articles_us) == (3, 3)
    items = {item.title: item for item in (await db_session.execute(
        select(NewsItem).where(NewsItem.day == TARGET))).scalars()}
    assert set(items) == {"Chip rally", "Memory demand", "No link here"}
    assert items["Chip rally"].link == "https://News.Example.com/a?utm=1#top"
    assert items["Chip rally"].source_domain == "news.example.com"
    assert items["No link here"].link is None
    assert items["Memory demand"].tickers == ["MU.US", "NVDA.US"] and items["Memory demand"].symbol_count == 2

    snapshot = await db_session.get(RawDataSnapshot, day.raw_snapshot_id)
    assert (snapshot.source, snapshot.dataset, snapshot.as_of_date) == ("EODHD", "news_market_feed", TARGET)
    with gzip.open(snapshot.storage_path, "rt", encoding="utf-8") as handle:
        raw_rows = json.load(handle)
    assert len(raw_rows) == 5
    assert all(set(row) == {"date", "title", "link", "symbols", "tags"} for row in raw_rows)
    assert [row["date"] for row in raw_rows] == sorted(row["date"] for row in raw_rows)


async def test_titles_are_unescaped_for_storage_and_dedupe_but_lineage_keeps_provider_text(feed, db_session):
    feed.pages[("2026-10-09", 0)] = [
        article("2026-10-09T13:00:00Z", "AI &amp; Data Center Spending  Concerns", "https://news.example.com/a"),
        article("2026-10-09T13:05:00Z", "AI & Data Center Spending Concerns", "https://other.example/b"),
    ]

    await ingest_market_news(TARGET, max_backfill_days=0)

    titles = [item.title for item in (await db_session.execute(
        select(NewsItem).where(NewsItem.day == TARGET))).scalars()]
    assert titles == ["AI & Data Center Spending Concerns"]
    day = await ingest_day(db_session, TARGET)
    snapshot = await db_session.get(RawDataSnapshot, day.raw_snapshot_id)
    with gzip.open(snapshot.storage_path, "rt", encoding="utf-8") as handle:
        raw_titles = {row["title"] for row in json.load(handle)}
    assert "AI &amp; Data Center Spending  Concerns" in raw_titles


async def test_us_tickers_are_canonical_and_focused_counts_respect_symbol_limit(feed, db_session):
    feed.pages[("2026-10-09", 0)] = [
        article("2026-10-09T13:00:00Z", "Berkshire buys", "https://a.example/1",
                symbols=("BRK.B.US", "brk/b.us", "AAPL.US", "AAPL.MX", "2RR.F")),
        article("2026-10-09T14:00:00Z", "Broad market wrap", "https://a.example/2",
                symbols=("AAPL.US", "MSFT.US", "NVDA.US", "AMZN.US", "META.US", "GOOGL.US")),
    ]

    await ingest_market_news(TARGET, max_backfill_days=0)

    counts = await day_counts(db_session, TARGET)
    assert counts["BRK-B.US"] == (1, 1)
    assert counts["AAPL.US"] == (2, 1)
    assert counts["MSFT.US"] == (1, 0)
    assert not any(not ticker.endswith(".US") for ticker in counts)


async def test_articles_count_only_on_their_own_utc_day_of_their_own_request(feed, db_session):
    feed.pages[("2026-10-09", 0)] = [
        article("2026-10-09T15:00:00Z", "Intel news", "https://a.example/i", symbols=("INTC.US",)),
        article("2026-10-08T23:30:00-04:00", "Late NVDA", "https://a.example/n"),  # UTC 10-09 03:30
        article("2026-10-09T22:30:00-04:00", "Next-day AMD", "https://a.example/x", symbols=("AMD.US",)),
    ]
    feed.pages[("2026-10-08", 0)] = [
        article("2026-10-08T12:00:00+00:00", "AMD midday", "https://a.example/a", symbols=("AMD.US",)),
        article("2026-10-08T22:00:00", "Naive MU", "https://a.example/m", symbols=("MU.US",)),  # naive = UTC
        article("2026-10-08T23:30:00-04:00", "Spilled TSLA", "https://a.example/s", symbols=("TSLA.US",)),
        article("2026-10-07T22:00:00Z", "Early PLTR", "https://a.example/p", symbols=("PLTR.US",)),
        article("not-a-date", "Undated", "https://a.example/u", symbols=("ORCL.US",)),
    ]

    result = await ingest_market_news(TARGET, max_backfill_days=0)

    assert await day_counts(db_session, date(2026, 10, 9)) == {"INTC.US": (1, 1), "NVDA.US": (1, 1)}
    assert await day_counts(db_session, date(2026, 10, 8)) == {"AMD.US": (1, 1), "MU.US": (1, 1)}
    assert await day_counts(db_session, date(2026, 10, 7)) == {}
    assert (await db_session.execute(select(TickerAttentionDaily).where(
        TickerAttentionDaily.ticker.in_(["TSLA.US", "PLTR.US", "ORCL.US"])))).first() is None
    assert result["articles_off_day_ignored"] == 3 and result["rows_undated"] == 1
    run = await db_session.get(PipelineRun, result["run_id"])
    assert any("outside their requested UTC day" in warning for warning in run.quality_report["warnings"])
    late = await db_session.scalar(select(NewsItem).where(NewsItem.title == "Late NVDA"))
    assert late.day == date(2026, 10, 9) and late.published_at == datetime(2026, 10, 9, 3, 30)
    assert late.published_at.tzinfo is None
    # Every saved headline points at the snapshot of the request that returned it.
    for item in (await db_session.execute(select(NewsItem))).scalars():
        snapshot = await db_session.get(RawDataSnapshot, item.raw_snapshot_id)
        assert snapshot.as_of_date == item.day
        with gzip.open(snapshot.storage_path, "rt", encoding="utf-8") as handle:
            assert item.title in {row["title"] for row in json.load(handle)}
    # Lineage keeps everything the 10-08 request returned.
    day = await ingest_day(db_session, date(2026, 10, 8))
    assert day.articles_total == 2
    snapshot = await db_session.get(RawDataSnapshot, day.raw_snapshot_id)
    with gzip.open(snapshot.storage_path, "rt", encoding="utf-8") as handle:
        assert len(json.load(handle)) == 5


async def test_day_counts_do_not_depend_on_which_neighbours_the_run_fetched(feed, monkeypatch, db_session):
    feed.pages[("2026-10-06", 0)] = [
        article("2026-10-06T12:00:00Z", "Monday NVDA", "https://a.example/6"),
        article("2026-10-06T23:30:00-04:00", "Spill into 10-07", "https://a.example/spill"),  # UTC 10-07 03:30
    ]
    feed.pages[("2026-10-07", 0)] = [article("2026-10-07T12:00:00Z", "Tuesday NVDA", "https://a.example/7")]
    monkeypatch.setattr(news_ingest, "utc_now", lambda: datetime(2026, 10, 9, 9, 0))

    first = await ingest_market_news(date(2026, 10, 8), max_backfill_days=0)  # fetches 10-08, 10-07, 10-06
    assert first["recent_days"] == ["2026-10-06", "2026-10-07", "2026-10-08"]
    after_first = await day_counts(db_session, date(2026, 10, 7))

    monkeypatch.setattr(news_ingest, "utc_now", lambda: NOW)
    second = await ingest_market_news(TARGET, max_backfill_days=0)  # fetches 10-09, 10-08, 10-07 only
    assert second["recent_days"] == ["2026-10-07", "2026-10-08", "2026-10-09"]

    db_session.expire_all()
    assert after_first == await day_counts(db_session, date(2026, 10, 7)) == {"NVDA.US": (1, 1)}
    day = await ingest_day(db_session, date(2026, 10, 7))
    assert (day.status, day.articles_total, day.source_run_id) == ("complete", 1, second["run_id"])


async def test_retention_deletes_old_items_and_skips_storing_old_days(feed, monkeypatch, db_session):
    monkeypatch.setattr(settings, "THEME_NEWS_RETENTION_DAYS", 7)  # cutoff 2026-10-03
    monkeypatch.setattr(settings, "THEME_NEWS_BACKFILL_START", date(2026, 10, 1))
    for key, day in (("old", date(2026, 9, 1)), ("fresh", date(2026, 10, 5))):
        db_session.add(NewsItem(url_hash=key, day=day, published_at=datetime.combine(day, datetime.min.time()),
                                title=key, tickers=[], symbol_count=0))
    # 10-05 is already complete, so this run does not refetch (and replace) its headlines.
    db_session.add(NewsIngestDay(day=date(2026, 10, 5), status="complete", articles_total=1, articles_us=0))
    await db_session.commit()
    feed.pages[("2026-10-02", 0)] = [article("2026-10-02T10:00:00Z", "Old day story", "https://a.example/o")]
    feed.pages[("2026-10-04", 0)] = [article("2026-10-04T10:00:00Z", "Kept story", "https://a.example/k")]

    result = await ingest_market_news(TARGET)

    assert "2026-10-05" not in feed.days_called()
    hashes = set((await db_session.execute(select(NewsItem.url_hash))).scalars())
    assert "old" not in hashes and "fresh" in hashes
    titles = set((await db_session.execute(select(NewsItem.title))).scalars())
    assert "Kept story" in titles and "Old day story" not in titles
    assert await day_counts(db_session, date(2026, 10, 2)) == {"NVDA.US": (1, 1)}
    assert result["news_items_deleted"] == 1


async def test_reingest_replaces_the_days_counts(feed, db_session):
    feed.pages[("2026-10-09", 0)] = [
        article("2026-10-09T10:00:00Z", "One", "https://a.example/1"),
        article("2026-10-09T11:00:00Z", "Two", "https://a.example/2"),
        article("2026-10-09T12:00:00Z", "Three", "https://a.example/3", symbols=("AMD.US",)),
    ]
    first = await ingest_market_news(TARGET, max_backfill_days=0)
    assert await day_counts(db_session, TARGET) == {"NVDA.US": (2, 2), "AMD.US": (1, 1)}

    feed.pages[("2026-10-09", 0)] = [
        article("2026-10-09T10:00:00Z", "One", "https://a.example/1"),
        article("2026-10-09T13:00:00Z", "Four", "https://a.example/4", symbols=("MU.US",)),
    ]
    second = await ingest_market_news(TARGET, max_backfill_days=0)

    db_session.expire_all()
    assert await day_counts(db_session, TARGET) == {"NVDA.US": (1, 1), "MU.US": (1, 1)}
    rows = (await db_session.execute(select(TickerAttentionDaily).where(TickerAttentionDaily.day == TARGET))).scalars()
    assert {row.source_run_id for row in rows} == {second["run_id"]}
    day = await ingest_day(db_session, TARGET)
    assert day.articles_total == 2 and day.source_run_id == second["run_id"] != first["run_id"]
    # Saved headlines are replaced with the day's counts.
    titles = set((await db_session.execute(select(NewsItem.title).where(NewsItem.day == TARGET))).scalars())
    assert titles == {"One", "Four"}


async def test_publishes_only_once_all_seven_window_days_are_complete(feed, db_session):
    feed.pages[("2026-10-04", 0)] = None
    feed.pages[("2026-10-03", 0)] = None
    first = await ingest_market_news(TARGET, max_backfill_days=0)

    assert feed.days_called() == [day.isoformat() for day in reversed(WINDOW)]
    assert first["status"] == "partial" and first["published"] is False
    assert first["window_missing"] == ["2026-10-03", "2026-10-04"]
    assert first["backfill_remaining"] == 2
    assert await latest_published_date(NEWS_ATTENTION_DATASET) is None

    feed.calls.clear()
    feed.pages.clear()
    second = await ingest_market_news(TARGET, max_backfill_days=0)

    assert feed.days_called() == ["2026-10-09", "2026-10-08", "2026-10-07", "2026-10-04", "2026-10-03"]
    assert second["status"] == "published" and second["window_missing"] == []
    assert second["backfill_remaining"] == 0
    assert await latest_published_date(NEWS_ATTENTION_DATASET) == TARGET
    publication = await db_session.scalar(select(DataPublication).where(DataPublication.dataset == NEWS_ATTENTION_DATASET))
    assert (publication.as_of_date, publication.pipeline_run_id) == (TARGET, second["run_id"])
    run = await db_session.get(PipelineRun, second["run_id"])
    assert (run.pipeline_name, run.status) == ("news_attention", "published")
    assert run.quality_report["passed"] is True and run.quality_report["metrics"]["days_complete"] == 5
    stale = await db_session.get(PipelineRun, first["run_id"])
    assert stale.status == "partial"


async def test_backfill_limit_caps_only_history_older_than_the_window(feed, monkeypatch, db_session):
    monkeypatch.setattr(settings, "THEME_NEWS_BACKFILL_START", date(2026, 9, 28))

    result = await ingest_market_news(TARGET, max_backfill_days=2)

    # The window's four days behind the recent refresh do not use the budget; it covers 10-02 and 10-01.
    assert feed.days_called() == [(TARGET - timedelta(days=offset)).isoformat() for offset in range(9)]
    assert result["backfill_days"] == 6 and result["backfill_remaining"] == 3
    assert result["status"] == "published"


async def test_window_day_between_recent_refreshes_is_fetched_without_a_backfill_budget(feed, monkeypatch, db_session):
    """Labor Day week: the Tuesday run is skipped (its target, Friday, is published), so UTC
    Saturday 09-05 falls between the Saturday and Wednesday recent refreshes."""
    monkeypatch.setattr(settings, "THEME_NEWS_BACKFILL_START", date(2026, 8, 1))
    db_session.add_all([NewsIngestDay(day=date(2026, 8, 26) + timedelta(days=offset), status="complete",
                                      articles_total=0, articles_us=0) for offset in range(7)])
    await db_session.commit()

    monkeypatch.setattr(news_ingest, "utc_now", lambda: datetime(2026, 9, 5, 8, 40))
    friday = await ingest_market_news(date(2026, 9, 4), max_backfill_days=0)
    assert feed.days_called() == ["2026-09-04", "2026-09-03", "2026-09-02"]
    assert friday["status"] == "published"

    feed.calls.clear()
    monkeypatch.setattr(news_ingest, "utc_now", lambda: datetime(2026, 9, 9, 8, 40))
    tuesday = await ingest_market_news(date(2026, 9, 8), max_backfill_days=0)

    assert feed.days_called() == ["2026-09-08", "2026-09-07", "2026-09-06", "2026-09-05"]
    assert tuesday["status"] == "published" and tuesday["window_missing"] == []
    assert await latest_published_date(NEWS_ATTENTION_DATASET) == date(2026, 9, 8)


async def test_publishes_a_historical_target_from_the_target_plus_one_bound(feed, monkeypatch, db_session):
    monkeypatch.setattr(news_ingest, "utc_now", lambda: datetime(2026, 10, 20, 9, 0))  # long after the target

    result = await ingest_market_news(TARGET)

    # Recent refresh ends at target + 1 (not yesterday); backfill continues just below it.
    assert feed.days_called() == [(date(2026, 10, 10) - timedelta(days=offset)).isoformat() for offset in range(8)]
    assert result["recent_days"] == ["2026-10-08", "2026-10-09", "2026-10-10"]
    assert result["backfill_days"] == 5 and result["backfill_remaining"] == 0
    assert result["window_start"] == "2026-10-03" and result["window_missing"] == []
    assert result["status"] == "published"
    assert await latest_published_date(NEWS_ATTENTION_DATASET) == TARGET


async def test_bad_rows_and_errors_on_backfill_days_do_not_block_publication(feed, monkeypatch, db_session):
    monkeypatch.setattr(settings, "THEME_NEWS_BACKFILL_START", date(2026, 9, 30))
    feed.pages[("2026-10-02", 0)] = [
        article("2026-10-02T10:00:00Z", "Bad link", "https://example.com]/story"),
        article("2026-10-02T10:30:00Z", "Bad port", "https://example.com\uff1a8080/a", symbols=("MU.US",)),
        article("0001-01-01T00:30:00+01:00", "Out of range", "https://a.example/old"),
        article("2026-10-02T11:00:00Z", "Broken \ud800 title", "https://a.example/s\ud800", symbols=("AMD.US",)),
    ]
    feed.pages[("2026-10-01", 0)] = RuntimeError("unexpected payload")
    real_persist = news_ingest.persist_snapshot

    async def persist_or_fail(db, source, dataset, rows, *, as_of_date, details):
        if as_of_date == date(2026, 9, 30):
            raise RuntimeError("disk full")
        return await real_persist(db, source, dataset, rows, as_of_date=as_of_date, details=details)

    monkeypatch.setattr(news_ingest, "persist_snapshot", persist_or_fail)

    result = await ingest_market_news(TARGET)

    assert feed.days_called()[-3:] == ["2026-10-02", "2026-10-01", "2026-09-30"]
    assert result["status"] == "published" and result["window_missing"] == []
    assert await latest_published_date(NEWS_ATTENTION_DATASET) == TARGET
    assert result["rows_undated"] == 1 and result["days_failed"] == 1 and result["days_errored"] == 1
    assert await day_counts(db_session, date(2026, 10, 2)) == {"NVDA.US": (1, 1), "MU.US": (1, 1), "AMD.US": (1, 1)}
    items = {item.title: item for item in (await db_session.execute(
        select(NewsItem).where(NewsItem.day == date(2026, 10, 2)))).scalars()}
    assert items["Bad link"].link is None and items["Bad port"].link is None
    assert "\ud800" not in items["Broken ? title"].title + items["Broken ? title"].link
    statuses = dict((await db_session.execute(select(NewsIngestDay.day, NewsIngestDay.status))).all())
    assert statuses[date(2026, 10, 2)] == "complete" and statuses[date(2026, 10, 1)] == "failed"
    assert date(2026, 9, 30) not in statuses  # its transaction rolled back; the next run plans it again
    run = await db_session.get(PipelineRun, result["run_id"])
    assert run.status == "published"
    warning = next(text for text in run.quality_report["warnings"] if text.startswith("Unexpected ingestion errors"))
    assert "2026-10-01 fetch: RuntimeError" in warning and "2026-09-30 write: RuntimeError: disk full" in warning


async def test_an_emptied_refetch_keeps_the_complete_day(feed, db_session):
    feed.pages[("2026-10-08", 0)] = [
        article(f"2026-10-08T1{index}:00:00Z", f"Story {index}", f"https://a.example/{index}") for index in range(4)
    ]
    first = await ingest_market_news(TARGET, max_backfill_days=0)
    assert await day_counts(db_session, date(2026, 10, 8)) == {"NVDA.US": (4, 4)}

    feed.pages[("2026-10-08", 0)] = []
    emptied = await ingest_market_news(TARGET, max_backfill_days=0)
    feed.pages[("2026-10-08", 0)] = [article("2026-10-08T10:00:00Z", "Story 0", "https://a.example/0")]
    shrunk = await ingest_market_news(TARGET, max_backfill_days=0)

    assert emptied["days_refetch_shrunk"] == shrunk["days_refetch_shrunk"] == 1
    db_session.expire_all()
    day = await ingest_day(db_session, date(2026, 10, 8))
    assert (day.status, day.articles_total, day.source_run_id) == ("complete", 4, first["run_id"])
    assert await day_counts(db_session, date(2026, 10, 8)) == {"NVDA.US": (4, 4)}
    attention = await load_attention(db_session, date(2026, 10, 8), date(2026, 10, 8), tickers=["NVDA.US"])
    assert attention.counts.loc[date(2026, 10, 8), "NVDA.US"] == 4.0
    run = await db_session.get(PipelineRun, emptied["run_id"])
    assert any(text.startswith("Refetch returned far fewer articles") for text in run.quality_report["warnings"])


async def test_a_link_counts_on_one_day_only_across_days_and_runs(feed, monkeypatch, db_session):
    monkeypatch.setattr(settings, "THEME_NEWS_BACKFILL_START", date(2026, 10, 1))
    feed.pages[("2026-10-09", 0)] = [article("2026-10-09T01:00:00Z", "Chip story", "https://a.example/same")]
    feed.pages[("2026-10-08", 0)] = [article("2026-10-08T22:00:00Z", "Chip story", "https://a.example/same?r=1")]
    feed.pages[("2026-10-02", 0)] = [article("2026-10-02T12:00:00Z", "Memory story", "https://a.example/mem")]
    feed.pages[("2026-10-01", 0)] = [
        article("2026-10-01T23:00:00Z", "Memory story, early", "https://a.example/mem"),
        article("2026-10-01T12:00:00Z", "Other story", "https://a.example/other", symbols=("AMD.US",)),
    ]

    await ingest_market_news(TARGET, max_backfill_days=1)  # the window, then backfills 10-02 only
    await ingest_market_news(TARGET, max_backfill_days=1)  # backfills 10-01 in a later run

    db_session.expire_all()
    assert await day_counts(db_session, date(2026, 10, 9)) == {"NVDA.US": (1, 1)}
    assert await day_counts(db_session, date(2026, 10, 8)) == {}
    assert await day_counts(db_session, date(2026, 10, 2)) == {"NVDA.US": (1, 1)}
    assert await day_counts(db_session, date(2026, 10, 1)) == {"AMD.US": (1, 1)}
    items = (await db_session.execute(select(NewsItem.day, NewsItem.title).order_by(NewsItem.day))).all()
    assert items == [(date(2026, 10, 1), "Other story"), (date(2026, 10, 2), "Memory story"),
                     (date(2026, 10, 9), "Chip story")]


async def test_duplicates_merge_their_us_tickers_into_the_kept_copy(feed, db_session):
    feed.pages[("2026-10-09", 0)] = [
        article("2026-10-09T07:00:00Z", "Apple beats estimates", "https://a.example/apple", symbols=("AAPL.MX",)),
        article("2026-10-09T08:00:00Z", "Apple beats estimates", "https://b.example/apple", symbols=("AAPL.US",)),
        article("2026-10-09T09:00:00Z", "Chip roundup", "https://a.example/chips",
                symbols=("NVDA.US", "AMD.US", "MU.US")),
        article("2026-10-09T09:30:00Z", "Chip roundup (updated)", "https://a.example/chips?v=2",
                symbols=("INTC.US", "AVGO.US", "TSM.US")),
    ]

    await ingest_market_news(TARGET, max_backfill_days=0)

    counts = await day_counts(db_session, TARGET)
    assert counts["AAPL.US"] == (1, 1)
    # Six merged US tickers exceed the focused limit of five.
    assert {ticker: counts[ticker] for ticker in ("NVDA.US", "INTC.US", "TSM.US")} == {
        "NVDA.US": (1, 0), "INTC.US": (1, 0), "TSM.US": (1, 0)}
    day = await ingest_day(db_session, TARGET)
    assert (day.articles_total, day.articles_us) == (2, 2)
    roundup = await db_session.scalar(select(NewsItem).where(NewsItem.title == "Chip roundup"))
    assert roundup.symbol_count == 6 and "AVGO.US" in roundup.tickers


async def test_stops_after_consecutive_unavailable_days_and_still_reports(feed, monkeypatch, db_session):
    monkeypatch.setattr(settings, "THEME_NEWS_BACKFILL_START", date(2026, 9, 1))
    feed.default = None

    result = await ingest_market_news(TARGET)

    assert feed.days_called() == ["2026-10-09", "2026-10-08"]
    assert result["status"] == "failed" and result["days_failed"] == 2
    assert result["days_skipped"] == 1 + (date(2026, 10, 6) - date(2026, 9, 1)).days + 1
    run = await db_session.get(PipelineRun, result["run_id"])
    assert run.status == "failed"
    assert any("skipped 37 days (2026-09-01 to 2026-10-07)" in text for text in run.quality_report["warnings"])
    statuses = dict((await db_session.execute(select(NewsIngestDay.day, NewsIngestDay.status))).all())
    assert statuses == {date(2026, 10, 9): "failed", date(2026, 10, 8): "failed"}


async def test_quota_error_stops_marks_run_failed_and_propagates(feed, db_session):
    feed.pages[("2026-10-09", 0)] = [article("2026-10-09T10:00:00Z", "One", "https://a.example/1")]
    feed.pages[("2026-10-08", 0)] = [article("2026-10-08T10:00:00Z", "Two", "https://a.example/2")]
    feed.pages[("2026-10-07", 0)] = EODHDQuotaExceeded("quota exhausted")

    with pytest.raises(EODHDQuotaExceeded):
        await ingest_market_news(TARGET)

    assert feed.days_called() == ["2026-10-09", "2026-10-08", "2026-10-07"]
    run = (await db_session.execute(select(PipelineRun))).scalar_one()
    assert (run.pipeline_name, run.status) == ("news_attention", "failed")
    assert "limit is exhausted" in run.error_message
    assert run.quality_report["metrics"]["quota_exhausted"] is True
    assert (await db_session.execute(select(DataPublication))).first() is None
    # Days whose pages all arrived before the quota ran out are kept.
    statuses = dict((await db_session.execute(select(NewsIngestDay.day, NewsIngestDay.status))).all())
    assert statuses == {date(2026, 10, 9): "complete", date(2026, 10, 8): "complete"}


async def test_eodhd_token_never_reaches_logs_or_run_records(clock, monkeypatch, caplog, db_session):
    sentinel = "sentinel-token-7f3a9c"
    monkeypatch.setattr(eodhd_client, "EODHD_API_KEY", sentinel)
    monkeypatch.setattr(eodhd_client, "_quota_exhausted_on", None)
    monkeypatch.setattr(eodhd_client, "MAX_RETRIES", 1)
    monkeypatch.setattr(settings, "THEME_NEWS_BACKFILL_START", date(2026, 10, 6))
    seen_tokens = []

    def handler(request):
        seen_tokens.append(request.url.params.get("api_token"))
        day = request.url.params.get("from")
        if day == "2026-10-09":
            return httpx.Response(503, text="unavailable")
        if day == "2026-10-08":
            return httpx.Response(200, json=[article("2026-10-08T10:00:00Z", "One", "https://a.example/1")])
        if day == "2026-10-07":
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(402, text="limit")

    monkeypatch.setattr(
        news_ingest, "_create_news_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    caplog.set_level(logging.DEBUG)

    with pytest.raises(EODHDQuotaExceeded):
        await ingest_market_news(TARGET)

    assert seen_tokens == [sentinel] * 4
    assert "day marked failed" in caplog.text and "limit is exhausted" in caplog.text
    assert sentinel not in caplog.text
    run = (await db_session.execute(select(PipelineRun))).scalar_one()
    assert sentinel not in (run.error_message or "")
    assert sentinel not in json.dumps(run.quality_report)
    statuses = dict((await db_session.execute(select(NewsIngestDay.day, NewsIngestDay.status))).all())
    assert statuses == {date(2026, 10, 9): "failed", date(2026, 10, 8): "complete", date(2026, 10, 7): "failed"}


async def test_load_attention_zero_fills_complete_days_and_leaves_others_nan(db_session):
    days = [date(2026, 10, 1) + timedelta(days=offset) for offset in range(5)]
    db_session.add_all([
        NewsIngestDay(day=days[0], status="complete", articles_us=3),
        NewsIngestDay(day=days[1], status="partial", articles_us=5, truncated=True),
        NewsIngestDay(day=days[2], status="failed"),
        NewsIngestDay(day=days[4], status="complete", articles_us=0),
        TickerAttentionDaily(ticker="NVDA.US", day=days[0], article_count=2, focused_count=1),
        TickerAttentionDaily(ticker="AMD.US", day=days[0], article_count=1, focused_count=1),
        TickerAttentionDaily(ticker="NVDA.US", day=days[1], article_count=4, focused_count=4),
        TickerAttentionDaily(ticker="MU.US", day=days[1], article_count=1, focused_count=1),
    ])
    await db_session.commit()

    attention = await load_attention(db_session, days[0], days[4])

    assert list(attention.counts.index) == days
    assert list(attention.counts.columns) == ["AMD.US", "NVDA.US"]
    assert attention.counts.loc[days[0]].tolist() == [1.0, 2.0]
    assert attention.counts.loc[days[4]].tolist() == [0.0, 0.0]
    for day in days[1:4]:  # partial, failed and never-ingested days are unknown, not zero
        assert attention.counts.loc[day].isna().all()
    assert attention.totals.loc[days[0]] == 3.0 and attention.totals.loc[days[4]] == 0.0
    assert attention.totals.loc[days[1:4]].isna().all()
    assert attention.complete_days == {days[0], days[4]}

    requested = await load_attention(db_session, days[0], days[4], tickers=["NVDA.US", "TSLA.US", "NVDA.US"])
    assert list(requested.counts.columns) == ["NVDA.US", "TSLA.US"]
    assert requested.counts.loc[days[0]].tolist() == [2.0, 0.0]
    assert requested.counts.loc[days[1]].isna().all()

    empty = await load_attention(db_session, days[0], days[4], tickers=[])
    assert empty.counts.shape == (5, 0) and empty.complete_days == {days[0], days[4]}
