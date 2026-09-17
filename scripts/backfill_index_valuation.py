"""One-time data acquisition for the S&P 500 month-end P/E history.

For every ticker that was ever an S&P 500 member since
``INDEX_VALUATION_HISTORY_START``, this script makes the local database
capable of reconstructing that member's month-end multiples:

1. refresh strict point-in-time membership intervals from EODHD;
2. backfill daily prices over each member's own membership window;
3. fetch full fundamentals once for members without stored statements
   (current members already carry full statement history from the screener);
4. verify complete split history so share/price bases are provable;
5. run ``refresh_index_valuation`` for the target session.

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
import logging
import os
import sys
from datetime import date, timedelta
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert

from core.config import settings
from core.trading_calendar import is_us_market_session, latest_completed_us_session
from database import async_session_maker, init_db
from models import DailyPrice, FundamentalVersion, Ticker, UniverseMembership
from services import eodhd_client
from services.data_sync import _upsert_daily_prices, _upsert_financials, _upsert_ticker_info
from services.index_valuation import refresh_index_valuation
from services.pipeline_runs import begin_pipeline_run, finish_pipeline_run, update_pipeline_run
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


async def _has_window_statements(ticker: str, start: date, end: date) -> bool:
    async with async_session_maker() as db:
        rows = await db.scalar(
            select(func.count(FundamentalVersion.id)).where(
                FundamentalVersion.ticker == ticker,
                FundamentalVersion.period_type == "Quarterly",
                FundamentalVersion.period_end >= start - timedelta(days=STATEMENT_LOOKBACK_DAYS),
                FundamentalVersion.period_end <= end,
            )
        )
    return bool(rows)


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
            "metrics": stats,
            "errors": [] if stats["failed"] == 0 else [f"{stats['failed']} members failed"],
            "warnings": [],
        }
        await finish_pipeline_run(run_id, "published", quality_report=quality)
        return {"run_id": run_id, **stats}
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

    if not args.skip_membership_refresh:
        logger.info("Refreshing strict S&P 500 membership intervals for %s...", target)
        result = await refresh_historical_universe_memberships(target)
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

    stats = await backfill_member_data(windows, target)
    logger.info("Backfill complete: %s", stats)

    if args.verify:
        for ticker in sorted(windows):
            await _verify_member(ticker)

    if args.skip_refresh:
        logger.info("Skipping aggregation refresh (--skip-refresh).")
        return 0

    logger.info("Running index valuation refresh for %s...", target)
    result = await refresh_index_valuation(target)
    logger.info("Index valuation refresh: %s", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
