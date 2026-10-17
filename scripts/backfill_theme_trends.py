"""One-time history backfill for trending themes (题材热度).

1. News: run ``ingest_market_news`` in bounded batches (``--news-batch-days`` older
   UTC days per batch) until every UTC day from ``THEME_NEWS_BACKFILL_START`` is
   complete, the provider stops making progress, or ``--max-news-batches`` is hit.
   Attention baselines need ~100 complete days before the first scored session.
2. Trends: ``refresh_theme_trends(target, backfill_sessions=N)`` scores the last N
   sessions under one run and publishes the target plus every earlier date that has
   no publication with member/trending-company rows. Dates before the first
   membership interval use today's members and carry ``pit=False``.

Every step is idempotent: complete news days are never refetched (except the
recent refresh window), and a trends re-run replaces earlier *backfill* rows while
keeping dates the daily job published (their members and trending companies exist
only for that date's own run). ``--overwrite`` republishes those dates too, which
deletes their member and trending-company rows for good; use it only to make the
whole history consistent, e.g. after a scoring version change. The worker must have
published ``screener``, ``price_history`` and ``theme_membership`` for the target
first (the daily pipeline plus the theme bootstrap do that).

Cost: ~5 EODHD calls per 1,000-article page, about 10 calls per UTC day.

Examples:
    python scripts/backfill_theme_trends.py --news-only
    python scripts/backfill_theme_trends.py --trends-only --sessions 60
    python scripts/backfill_theme_trends.py --backfill-prices
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import settings
from core.trading_calendar import is_us_market_session, latest_completed_us_session
from database import async_session_maker, init_db
from services.eodhd_client import EODHDQuotaExceeded
from services.history_backfill import backfill_price_history
from services.themes.membership import load_member_spans
from services.themes.news_ingest import ingest_market_news
from services.themes.pipeline import BENCHMARK_TICKER, SCORING_SESSIONS, refresh_theme_trends

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("backfill_theme_trends")

DEFAULT_MAX_NEWS_BATCHES = 30
NEW_YORK = ZoneInfo("America/New_York")


def resolve_target(target_date: Optional[date]) -> date:
    """The given session, else the latest completed session by the New York date
    (the scheduler's rule; the host clock is UTC in the container)."""
    if target_date is not None and is_us_market_session(target_date):
        return target_date
    return latest_completed_us_session(target_date or datetime.now(NEW_YORK).date())


async def backfill_news(target: date, *, batch_days: int, max_batches: int) -> dict:
    """Ingest news in bounded batches until the backfill start is covered.

    Stops when ``backfill_remaining`` reaches 0, when a batch makes no progress
    (days that stay partial or failed), after ``max_batches``, or when the EODHD
    quota is exhausted (reported, not raised).
    """
    batches = 0
    remaining: Optional[int] = None
    last: dict = {}
    stop_reason = "max-batches"
    while batches < max_batches:
        try:
            last = await ingest_market_news(target, max_backfill_days=batch_days)
        except EODHDQuotaExceeded:
            stop_reason = "quota-exhausted"
            logger.error("EODHD quota exhausted after %d news batches; re-run tomorrow to continue", batches)
            break
        batches += 1
        current = last.get("backfill_remaining")
        logger.info(
            "News batch %d: status=%s fetched=%s complete=%s failed=%s remaining=%s calls~%s",
            batches,
            last.get("status"),
            last.get("days_fetched"),
            last.get("days_complete"),
            last.get("days_failed"),
            current,
            last.get("calls_estimate"),
        )
        if current is None:
            stop_reason = "unknown-remaining"
            break
        if current <= 0:
            stop_reason = "covered"
            remaining = current
            break
        if remaining is not None and current >= remaining:
            stop_reason = "no-progress"
            remaining = current
            logger.warning(
                "News backfill made no progress (%s days still incomplete); "
                "those days stay partial or failed at the provider",
                current,
            )
            break
        remaining = current
    return {
        "batches": batches,
        "backfill_remaining": remaining,
        "stop_reason": stop_reason,
        "status": last.get("status"),
    }


async def backfill_member_prices(target: date, sessions: int) -> dict:
    """Optional: extend member + SPY daily prices to the window the trends backfill reads."""
    async with async_session_maker() as db:
        spans = await load_member_spans(db)
    tickers = sorted({span.ticker for span in spans} | {BENCHMARK_TICKER})
    if len(tickers) <= 1:
        return {"status": "skipped", "reason": "no-members"}
    return await backfill_price_history(
        tickers,
        history_days=SCORING_SESSIONS + sessions - 1,
        target_date=target,
        include_corporate_actions=False,
        include_target_session=True,
        publish_dataset=False,
        minimum_ticker_coverage=settings.PIPELINE_MIN_PRICE_FACTOR_COVERAGE,
    )


async def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--news-only", action="store_true", help="only backfill news attention days")
    scope.add_argument("--trends-only", action="store_true", help="only run the theme trends backfill")
    parser.add_argument("--sessions", type=int, default=settings.THEME_BACKFILL_SESSIONS,
                        help="trailing sessions to publish (default: THEME_BACKFILL_SESSIONS)")
    parser.add_argument("--target-date", type=date.fromisoformat, default=None,
                        help="session to backfill through (default: latest completed session)")
    parser.add_argument("--news-batch-days", type=int, default=settings.THEME_NEWS_BACKFILL_DAYS_PER_RUN,
                        help="older UTC days fetched per news batch")
    parser.add_argument("--max-news-batches", type=int, default=DEFAULT_MAX_NEWS_BATCHES)
    parser.add_argument("--backfill-prices", action="store_true",
                        help="first extend member and SPY daily prices to the scored window")
    parser.add_argument("--overwrite", action="store_true",
                        help="also republish dates the daily job published (deletes their member and "
                             "trending-company rows, which a backfill cannot recompute)")
    args = parser.parse_args(argv)
    if args.sessions < 1:
        parser.error("--sessions must be at least 1")
    if args.news_batch_days < 1 or args.max_news_batches < 1:
        parser.error("--news-batch-days and --max-news-batches must be at least 1")

    target = resolve_target(args.target_date)
    await init_db()
    logger.info("Theme backfill through %s (news from %s, %d sessions)",
                target, settings.THEME_NEWS_BACKFILL_START, args.sessions)

    if not args.trends_only:
        news = await backfill_news(target, batch_days=args.news_batch_days, max_batches=args.max_news_batches)
        logger.info("News backfill: %s", news)
        if args.news_only:
            return 0 if news["stop_reason"] == "covered" else 1
        if news["stop_reason"] != "covered":
            logger.warning("News history is incomplete; early sessions will carry degraded attention")

    if args.backfill_prices:
        try:
            prices = await backfill_member_prices(target, args.sessions)
            logger.info("Member price backfill: %s", prices.get("status"))
        except Exception as exc:
            # Coverage shortfalls only narrow the scored history; the trends gate decides.
            logger.warning("Member price backfill incomplete: %s", type(exc).__name__)

    result = await refresh_theme_trends(target, backfill_sessions=args.sessions, overwrite=args.overwrite)
    logger.info("Theme trends backfill: %s", {key: value for key, value in result.items() if key != "warnings"})
    for warning in result.get("warnings") or []:
        logger.warning(warning)
    if result.get("status") == "deferred":
        logger.error(
            "Theme trends deferred (missing: %s). Let the worker publish them for %s, then re-run with "
            "--trends-only; the news backfill above is kept.",
            ", ".join(result.get("missing", [])) or result.get("reason"), target,
        )
        return 2
    return 0 if result.get("status") == "published" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
