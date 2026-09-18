"""One-time data acquisition for the S&P 500 month-end P/E history.

For every ticker that was ever an S&P 500 member since
``INDEX_VALUATION_HISTORY_START``, this script makes the local database
capable of reconstructing that member's month-end multiples:

1. refresh strict point-in-time membership intervals from EODHD;
2. backfill daily prices over each member's own membership window;
3. fetch full fundamentals once for members without stored statements
   (current members already carry full statement history from the screener);
4. verify complete split history so share/price bases are provable;
5. restore legacy statement currencies only from each version's immutable raw
   fundamentals snapshot;
6. run ``refresh_index_valuation`` for the target session.

Every step is idempotent and safe to re-run after an interruption; completed
tickers are skipped. Call cost is dominated by one EOD call per member for
prices, one for splits, and ten per member that still needs fundamentals.

Examples:
    python scripts/backfill_index_valuation.py --dry-run
    python scripts/backfill_index_valuation.py --tickers AAPL.US,MSFT.US --verify
    python scripts/backfill_index_valuation.py
"""

import argparse
import asyncio
import gzip
import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert

from core.config import settings
from core.time_utils import utc_now
from core.trading_calendar import is_us_market_session, latest_completed_us_session
from database import async_session_maker, init_db
from models import (
    DailyPrice,
    FundamentalVersion,
    IndexValuationBackfillCheckpoint,
    Ticker,
    UniverseMembership,
)
from services import eodhd_client
from services.data_sync import _upsert_daily_prices, _upsert_financials, _upsert_ticker_info
from services.index_valuation import (
    completed_month_end_labels,
    last_session_of_month,
    refresh_index_valuation,
)
from services.fundamental_version_currency import (
    repair_fundamental_version_currencies,
)
from services.pipeline_runs import begin_pipeline_run, finish_pipeline_run, latest_published_date, update_pipeline_run
from services.raw_store import persist_snapshot
from services.split_history import load_split_history, sync_full_split_history
from services.universe import (
    HISTORICAL_UNIVERSE_SOURCE,
    refresh_historical_universe_memberships,
)
from services.valuation_history import get_valuation_history

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("backfill_index_valuation")

PRICE_COVERAGE_THRESHOLD = 0.90
# A member needs trailing quarters before its first month-end sample, so the
# statement-completion check looks almost a year behind the window start.
STATEMENT_LOOKBACK_DAYS = 370
STATEMENT_COMPLETION_RATIO = 0.75
# Fiscal calendars vary, so head/tail slack is a quarter plus drift and the
# gap check allows one missing-quarter-shaped hole to fail loudly.
STATEMENT_MAX_HEAD_SLACK_DAYS = 140
STATEMENT_MAX_TAIL_SLACK_DAYS = 140
STATEMENT_MAX_QUARTER_GAP_DAYS = 140
# After a successfully normalized fundamentals fetch, do not pay for another
# one inside this window even when the provider's own history remains incomplete.
FUNDAMENTALS_REFETCH_GUARD_DAYS = 7
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def _sessions_between(start: date, end: date) -> int:
    count = 0
    cursor = start
    while cursor <= end:
        if is_us_market_session(cursor):
            count += 1
        cursor += timedelta(days=1)
    return count


async def _load_membership_windows(target: date) -> dict[str, tuple[date, date]]:
    """Ever-members since the history start, each with its needed price window."""
    history_start = settings.INDEX_VALUATION_HISTORY_START
    async with async_session_maker() as db:
        rows = (await db.execute(
            select(
                UniverseMembership.ticker,
                UniverseMembership.effective_from,
                UniverseMembership.effective_to,
            ).where(
                UniverseMembership.universe == "SP500",
                UniverseMembership.source == HISTORICAL_UNIVERSE_SOURCE,
                UniverseMembership.effective_from <= target,
                (
                    UniverseMembership.effective_to.is_(None)
                    | (UniverseMembership.effective_to >= history_start)
                ),
            )
        )).all()
    windows: dict[str, tuple[date, date]] = {}
    for ticker, effective_from, effective_to in rows:
        window_start = max(effective_from, history_start)
        window_end = min(effective_to or target, target)
        if window_start > window_end:
            continue
        previous = windows.get(ticker)
        if previous is None:
            windows[ticker] = (window_start, window_end)
        else:
            windows[ticker] = (min(previous[0], window_start), max(previous[1], window_end))
    return windows


async def _price_coverage(ticker: str, start: date, end: date) -> float:
    async with async_session_maker() as db:
        rows = await db.scalar(
            select(func.count(DailyPrice.id)).where(
                DailyPrice.ticker == ticker,
                DailyPrice.date >= start,
                DailyPrice.date <= end,
                DailyPrice.close > 0,
            )
        )
    expected = _sessions_between(start, end)
    return (rows or 0) / expected if expected else 1.0


async def _ensure_sec_tickers_file() -> bool:
    """Download the official SEC company-tickers file when the cache is absent.

    The file is the primary multi-class grouping source and is not shipped in
    the image (data/ is excluded), so a fresh deployment must fetch it once.
    """
    path = Path(settings.INDEX_VALUATION_SEC_TICKERS_PATH)
    if path.exists():
        return True
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(
                SEC_TICKERS_URL, headers={"User-Agent": settings.SEC_USER_AGENT}
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict) or not payload:
            raise ValueError("unexpected company-tickers payload")
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle)
        logger.info("Downloaded SEC company-tickers file to %s", path)
        return True
    except Exception as exc:
        logger.warning(
            "Unable to download the SEC company-tickers file (%s): %s. "
            "Multi-class grouping will fall back to the documented static "
            "list; historical companies outside it may be double-counted in "
            "member counts.", SEC_TICKERS_URL, exc,
        )
        return False


async def _window_statement_coverage(ticker: str, start: date, end: date) -> tuple[list[date], Optional[date]]:
    """Distinct stored quarter-ends in the window span, plus the member's
    first stored price date (used to bound the span by its own listing)."""
    async with async_session_maker() as db:
        period_ends = list((await db.execute(
            select(FundamentalVersion.period_end).where(
                FundamentalVersion.ticker == ticker,
                FundamentalVersion.period_type == "Quarterly",
                FundamentalVersion.period_end >= start - timedelta(days=STATEMENT_LOOKBACK_DAYS),
                FundamentalVersion.period_end <= end,
            ).distinct().order_by(FundamentalVersion.period_end)
        )).scalars())
        first_price = await db.scalar(
            select(func.min(DailyPrice.date)).where(DailyPrice.ticker == ticker)
        )
    return period_ends, first_price


def statement_window_complete(
    period_ends: list[date],
    first_price: Optional[date],
    start: date,
    end: date,
) -> bool:
    """Stored quarters must cover the window span, not merely outnumber it.

    Beyond a minimum count, the earliest quarter must sit near the span start
    (a recent-only tail is exactly the partial-database case that would never
    self-heal), the latest quarter must sit near the span end, and no two
    adjacent quarters may be more than a quarter plus fiscal-calendar slack
    apart, because the aggregator needs four consecutive quarters behind
    every month-end.
    """
    span_start = start - timedelta(days=STATEMENT_LOOKBACK_DAYS)
    if first_price is not None:
        span_start = max(span_start, first_price - timedelta(days=130))
    if not period_ends:
        return False
    expected = max(1, (end - span_start).days // 91)
    if len(period_ends) < max(1, int(expected * STATEMENT_COMPLETION_RATIO)):
        return False
    if period_ends[0] > span_start + timedelta(days=STATEMENT_MAX_HEAD_SLACK_DAYS):
        return False
    if period_ends[-1] < end - timedelta(days=STATEMENT_MAX_TAIL_SLACK_DAYS):
        return False
    return all(
        later - earlier <= timedelta(days=STATEMENT_MAX_QUARTER_GAP_DAYS)
        for earlier, later in zip(period_ends, period_ends[1:])
    )


async def _recently_normalized_fundamentals(ticker: str) -> bool:
    """Whether fundamentals were normalized inside the re-fetch guard.

    This deliberately uses a mutable checkpoint instead of the immutable raw
    snapshot timestamp. Raw snapshots deduplicate identical payloads, so their
    original ``fetched_at`` cannot record a later repeated fetch; they are also
    committed before normalization and must not suppress recovery from a
    failed normalized write.
    """
    cutoff = utc_now() - timedelta(days=FUNDAMENTALS_REFETCH_GUARD_DAYS)
    async with async_session_maker() as db:
        normalized_at = await db.scalar(
            select(IndexValuationBackfillCheckpoint.fundamentals_normalized_at).where(
                IndexValuationBackfillCheckpoint.ticker == ticker,
                IndexValuationBackfillCheckpoint.fundamentals_normalized_at >= cutoff,
            )
        )
    return normalized_at is not None


async def _has_window_statements(ticker: str, start: date, end: date) -> bool:
    period_ends, first_price = await _window_statement_coverage(ticker, start, end)
    return statement_window_complete(period_ends, first_price, start, end)


async def _has_split_coverage(ticker: str, start: date, end: date) -> bool:
    async with async_session_maker() as db:
        coverage = await load_split_history(db, ticker, start, end)
    return coverage is not None


async def _fetch_prices(ticker: str, start: date, end: date, client) -> int:
    prices = await eodhd_client.get_eod_historical_data(
        ticker,
        (start - timedelta(days=7)).isoformat(),
        end.isoformat(),
        client=client,
    )
    if not prices:
        raise ValueError("provider returned no price history")
    async with async_session_maker() as db, db.begin():
        await persist_snapshot(
            db,
            "EODHD",
            "eod_prices",
            prices,
            as_of_date=end,
            details={
                "ticker": ticker,
                "from_date": (start - timedelta(days=7)).isoformat(),
                "to_date": end.isoformat(),
            },
        )
        await _upsert_daily_prices(ticker, prices, db)
    return len(prices)


async def _fetch_fundamentals(ticker: str, observed: date, client) -> None:
    payload = await eodhd_client.get_fundamental_data(ticker, client=client)
    if not payload:
        raise ValueError("provider returned no fundamentals")
    async with async_session_maker() as raw_db, raw_db.begin():
        await raw_db.execute(
            insert(Ticker)
            .values([{"ticker": ticker}])
            .on_conflict_do_nothing(index_elements=["ticker"])
        )
        snapshot = await persist_snapshot(
            raw_db,
            "EODHD",
            "fundamentals",
            payload,
            as_of_date=observed,
            details={"ticker": ticker},
        )
    async with async_session_maker() as db, db.begin():
        await _upsert_ticker_info(ticker, payload, db)
        await _upsert_financials(ticker, payload, db, raw_snapshot_id=snapshot.id)
        normalized_at = utc_now()
        checkpoint_stmt = insert(IndexValuationBackfillCheckpoint).values(
            ticker=ticker,
            fundamentals_normalized_at=normalized_at,
            raw_snapshot_id=snapshot.id,
        ).on_conflict_do_update(
            index_elements=["ticker"],
            set_={
                "fundamentals_normalized_at": normalized_at,
                "raw_snapshot_id": snapshot.id,
            },
        )
        await db.execute(checkpoint_stmt)


async def _fetch_splits(ticker: str) -> None:
    async with async_session_maker() as db:
        ok = await sync_full_split_history(db, ticker)
    if not ok:
        raise ValueError("complete split history could not be verified")


async def backfill_member_data(
    windows: dict[str, tuple[date, date]],
    target: date,
    *,
    concurrency: Optional[int] = None,
) -> dict:
    semaphore = asyncio.Semaphore(max(1, concurrency or settings.HISTORY_BACKFILL_CONCURRENCY))
    stats = {
        "members": len(windows),
        "prices_fetched": 0,
        "prices_skipped": 0,
        "fundamentals_fetched": 0,
        "fundamentals_skipped": 0,
        "splits_synced": 0,
        "splits_skipped": 0,
        "failed": 0,
    }
    failed_tickers: list[str] = []
    run_id = await begin_pipeline_run("index_valuation_backfill", target, version="v1")

    async def process(ticker: str) -> None:
        async with semaphore:
            start, end = windows[ticker]
            try:
                # Prices and corporate actions both carry ticker foreign keys.
                async with async_session_maker() as db, db.begin():
                    await db.execute(
                        insert(Ticker)
                        .values([{"ticker": ticker}])
                        .on_conflict_do_nothing(index_elements=["ticker"])
                    )
                coverage = await _price_coverage(ticker, start, end)
                if coverage >= PRICE_COVERAGE_THRESHOLD:
                    stats["prices_skipped"] += 1
                else:
                    rows = await _fetch_prices(ticker, start, end, client)
                    final_coverage = await _price_coverage(ticker, start, end)
                    if final_coverage < PRICE_COVERAGE_THRESHOLD:
                        logger.warning(
                            "%s price coverage %.0f%% after fetch (%d rows)",
                            ticker, 100 * final_coverage, rows,
                        )
                    stats["prices_fetched"] += 1
                if await _has_window_statements(ticker, start, end):
                    stats["fundamentals_skipped"] += 1
                elif await _recently_normalized_fundamentals(ticker):
                    # The completion check still fails after a fresh fetch:
                    # the provider itself lacks the quarters, and re-paying
                    # for the same payload every re-run would not fix that.
                    logger.warning(
                        "%s statement history remains incomplete after a fetch "
                        "within %d days; the provider likely lacks the quarters. "
                        "Skipping the paid re-fetch; the aggregation coverage "
                        "gates will judge the affected months.",
                        ticker, FUNDAMENTALS_REFETCH_GUARD_DAYS,
                    )
                    stats["fundamentals_skipped"] += 1
                else:
                    await _fetch_fundamentals(ticker, target, client)
                    stats["fundamentals_fetched"] += 1
                if await _has_split_coverage(ticker, start, end):
                    stats["splits_skipped"] += 1
                else:
                    await _fetch_splits(ticker)
                    stats["splits_synced"] += 1
            except Exception as exc:
                stats["failed"] += 1
                failed_tickers.append(ticker)
                logger.warning("Backfill failed for %s: %s", ticker, exc)

    try:
        await update_pipeline_run(run_id, "backfilling_members", 0)
        processed = 0
        # process() resolves `client` from this scope when gather runs it,
        # so the client must stay open for the whole batch loop.
        async with eodhd_client.create_http_client() as client:
            for batch_start in range(0, len(windows), 50):
                batch = list(windows)[batch_start:batch_start + 50]
                await asyncio.gather(*(process(ticker) for ticker in batch))
                processed += len(batch)
                await update_pipeline_run(
                    run_id, "backfilling_members", processed
                )
                logger.info(
                    "Backfilled %d/%d members (%s)",
                    processed, len(windows), stats,
                )
        quality = {
            "passed": stats["failed"] == 0,
            "metrics": {**stats, "failed_tickers": failed_tickers[:100]},
            "errors": [] if stats["failed"] == 0 else [
                f"{stats['failed']} members failed: " + ", ".join(sorted(failed_tickers)[:20])
            ],
            "warnings": [],
        }
        # Acquisition failures are recorded on the run even though the gated
        # index refresh may still publish: the run status must not claim every
        # member was acquired when it was not.
        await finish_pipeline_run(
            run_id,
            "published" if stats["failed"] == 0 else "failed",
            quality_report=quality,
            error_message=None if stats["failed"] == 0 else (
                f"{stats['failed']} members failed during acquisition"
            ),
        )
        return {"run_id": run_id, "status": "published" if stats["failed"] == 0 else "failed", **stats}
    except asyncio.CancelledError:
        await finish_pipeline_run(run_id, "cancelled", quality_report={"metrics": stats})
        raise
    except Exception as exc:
        await finish_pipeline_run(
            run_id, "failed", quality_report={"metrics": stats}, error_message=str(exc)
        )
        raise


async def _verify_member(ticker: str) -> None:
    async with async_session_maker() as db:
        history = await get_valuation_history(ticker, db, interval="1mo")
    points = history["points"][-3:]
    logger.info("%s latest month points:", ticker)
    for point in points:
        logger.info(
            "  %s price_date=%s equity=%s earnings_ttm=%s pe=%s reason=%s",
            point["date"], point["price_date"],
            None if point["equity"] is None else round(point["equity"], 1),
            None if point["earnings_ttm"] is None else round(point["earnings_ttm"], 1),
            None if point["values"]["pe"] is None else round(point["values"]["pe"], 2),
            point["earnings_reason"] or point["reason"],
        )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-date", type=date.fromisoformat, default=None)
    parser.add_argument("--tickers", type=str, default=None,
                        help="comma-separated member filter (smoke runs)")
    parser.add_argument("--limit", type=int, default=None,
                        help="only process the first N ever-members (smoke runs)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the work plan without fetching anything")
    parser.add_argument("--skip-membership-refresh", action="store_true")
    parser.add_argument("--verify", action="store_true",
                        help="print reconstructed month points for --tickers after the run")
    parser.add_argument("--skip-refresh", action="store_true",
                        help="stop after data acquisition, before the aggregation refresh")
    args = parser.parse_args()

    target = (
        args.target_date
        if args.target_date and is_us_market_session(args.target_date)
        else latest_completed_us_session(args.target_date or date.today())
    )
    history_start = settings.INDEX_VALUATION_HISTORY_START
    await init_db()

    # Fail fast before the expensive acquisition: the final aggregation
    # refresh defers unless the day's price_history publication exists, so a
    # cold deployment must run the daily pipeline (or cold_start_init.py) once
    # before this backfill.
    published_prices = await latest_published_date("price_history")
    if published_prices is None or published_prices < target:
        logger.error(
            "price_history is not published for %s (latest: %s). Run the daily "
            "screener pipeline or scripts/cold_start_init.py once, then re-run "
            "this backfill; otherwise the final refresh will defer.",
            target, published_prices,
        )
        if not args.skip_refresh:
            return 2

    if not args.skip_membership_refresh:
        # force=True: an older parser version may already have published this
        # session's intervals without the anchored ancient members, and the
        # skip-if-published guard would leave that truncated history in place.
        logger.info("Refreshing strict S&P 500 membership intervals for %s...", target)
        result = await refresh_historical_universe_memberships(target, force=True)
        logger.info("Membership refresh: %s", result.get("status"))

    windows = await _load_membership_windows(target)
    if not windows:
        logger.error(
            "No S&P 500 membership intervals overlap [%s, %s]; cannot backfill.",
            history_start, target,
        )
        return 1
    logger.info(
        "%d ever-members since %s need data through %s",
        len(windows), history_start, target,
    )
    first_sample = completed_month_end_labels(history_start, target)[:1]
    if first_sample:
        # Evaluate membership at the month's final trading session, exactly
        # like the aggregation does (a holiday calendar month-end misjudges
        # last-session exits and post-close joins).
        first_session = last_session_of_month(
            first_sample[0].year, first_sample[0].month
        ) or first_sample[0]
        async with async_session_maker() as db:
            early_members = await db.scalar(
                select(func.count(UniverseMembership.ticker.distinct())).where(
                    UniverseMembership.universe == "SP500",
                    UniverseMembership.source == HISTORICAL_UNIVERSE_SOURCE,
                    UniverseMembership.effective_from <= first_session,
                    (
                        UniverseMembership.effective_to.is_(None)
                        | (UniverseMembership.effective_to >= first_session)
                    ),
                )
            )
        if (early_members or 0) < settings.PIPELINE_MIN_SP500_SIZE:
            logger.warning(
                "Only %d members cover the first sample month %s (session %s, "
                "gate: %d). The provider history may be incomplete; the "
                "aggregation quality gate will decide whether early months "
                "stay gaps.",
                early_members, first_sample[0], first_session,
                settings.PIPELINE_MIN_SP500_SIZE,
            )

    if args.tickers:
        wanted = {ticker.strip().upper() for ticker in args.tickers.split(",")}
        windows = {ticker: window for ticker, window in windows.items() if ticker in wanted}
        missing = wanted - set(windows)
        if missing:
            logger.warning("Requested non-members ignored: %s", sorted(missing))
    if args.limit:
        windows = dict(list(windows.items())[: args.limit])

    if args.dry_run:
        for ticker, (start, end) in sorted(windows.items()):
            logger.info("DRY %s window=%s..%s", ticker, start, end)
        return 0

    await _ensure_sec_tickers_file()

    stats = await backfill_member_data(windows, target)
    logger.info("Backfill complete: %s", stats)
    if stats.get("failed"):
        logger.warning(
            "%d member(s) failed acquisition; the aggregation refresh will "
            "attempt anyway and its coverage gates decide the outcome. "
            "Re-run this script after fixing the provider errors to complete them.",
            stats["failed"],
        )

    currency_repair = await repair_fundamental_version_currencies()
    logger.info("Legacy fundamental currency repair: %s", currency_repair)

    if args.verify:
        for ticker in sorted(windows):
            await _verify_member(ticker)

    if args.skip_refresh:
        logger.info("Skipping aggregation refresh (--skip-refresh).")
        return 0

    logger.info("Running index valuation refresh for %s...", target)
    result = await refresh_index_valuation(target)
    logger.info("Index valuation refresh: %s", result)
    if result.get("status") == "deferred":
        logger.error(
            "The aggregation refresh deferred (missing: %s). Wait for the "
            "daily pipeline to publish the missing datasets for %s, or run "
            "scripts/cold_start_init.py, then re-run this script; the "
            "acquisition above is already complete and will be skipped.",
            ", ".join(result.get("missing", [])) or result.get("reason"), target,
        )
        return 2
    if result.get("status") != "published":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
