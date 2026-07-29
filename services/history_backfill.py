import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert

from core.config import settings
from core.trading_calendar import is_us_market_session, latest_completed_us_session
from database import async_session_maker
from models import DailyPrice, RawDataSnapshot, StockScreenerSnapshot, Ticker
from services import eodhd_client
from services.corporate_actions import upsert_corporate_actions
from services.data_sync import _upsert_daily_prices
from services.pipeline_runs import (
    begin_pipeline_run,
    finish_pipeline_run,
    publish_datasets_and_finish,
    update_pipeline_run,
    latest_published_date,
)
from services.raw_store import persist_snapshot


logger = logging.getLogger(__name__)
DIVIDEND_HISTORY_DATASET = "dividend_history_7y_v1"


def has_unpublished_market_session_gap(previous: date, target: date) -> bool:
    """Return whether more than one market session elapsed after a publication."""
    session_count = 0
    cursor = previous
    while cursor < target and session_count < 2:
        cursor += timedelta(days=1)
        if is_us_market_session(cursor):
            session_count += 1
    return session_count > 1


async def backfill_dividend_history_once(
    tickers: Iterable[str],
    target_date: Optional[date] = None,
    required_through_date: Optional[date] = None,
) -> dict:
    """Populate the expanded dividend window once, with retry-safe per-ticker writes."""
    target = target_date or date.today()
    symbols = sorted({ticker.upper() for ticker in tickers})
    if not symbols:
        return {"status": "deferred", "reason": "empty-universe"}

    async with async_session_maker() as security_db, security_db.begin():
        await security_db.execute(
            insert(Ticker)
            .values([{"ticker": ticker} for ticker in symbols])
            .on_conflict_do_nothing(index_elements=["ticker"])
        )

    dividend_start = target - timedelta(days=365 * 7)
    async with async_session_maker() as coverage_db:
        coverage_result = await coverage_db.execute(
            select(RawDataSnapshot.details).where(
                RawDataSnapshot.source == "EODHD",
                RawDataSnapshot.dataset == "dividends",
            )
        )
        covered = {
            str(details.get("ticker")).upper()
            for details in coverage_result.scalars()
            if isinstance(details, dict)
            and details.get("window_version") == "7y_v1"
            and details.get("ticker")
            and str(details.get("from_date") or "") <= dividend_start.isoformat()
            and (
                required_through_date is None
                or str(details.get("to_date") or "") >= required_through_date.isoformat()
            )
        }
    pending = [ticker for ticker in symbols if ticker not in covered]
    published = await latest_published_date(DIVIDEND_HISTORY_DATASET)
    if published is not None and not pending:
        return {
            "status": "skipped",
            "reason": "already-published",
            "as_of_date": published.isoformat(),
            "already_complete": len(symbols),
        }

    run_id = await begin_pipeline_run("dividend_history_backfill", target, version="v1")
    semaphore = asyncio.Semaphore(settings.HISTORY_BACKFILL_CONCURRENCY)
    stats = {
        "requested": len(symbols),
        "already_complete": len(symbols) - len(pending),
        "attempted": len(pending),
        "succeeded": 0,
        "failed": 0,
        "corporate_actions": 0,
    }

    async def process(ticker: str) -> None:
        async with semaphore:
            try:
                dividends = await eodhd_client.get_dividends(
                    ticker,
                    dividend_start.isoformat(),
                    target.isoformat(),
                    client=client,
                )
                if dividends is None:
                    raise ValueError("provider failed to return dividends")
                identity = {
                    "ticker": ticker,
                    "from_date": dividend_start.isoformat(),
                    "to_date": target.isoformat(),
                    "window_version": "7y_v1",
                }
                async with async_session_maker() as db, db.begin():
                    await persist_snapshot(
                        db,
                        "EODHD",
                        "dividends",
                        dividends,
                        as_of_date=target,
                        details=identity,
                    )
                    action_count = await upsert_corporate_actions(
                        db,
                        ticker,
                        [],
                        dividends,
                    )
                stats["succeeded"] += 1
                stats["corporate_actions"] += action_count
            except Exception as exc:
                stats["failed"] += 1
                logger.warning("Dividend history backfill failed for %s: %s", ticker, exc)

    try:
        await update_pipeline_run(run_id, "backfilling_dividends", 0)
        async with eodhd_client.create_http_client() as client:
            for start in range(0, len(pending), 250):
                await asyncio.gather(*(process(ticker) for ticker in pending[start:start + 250]))
                await update_pipeline_run(
                    run_id,
                    "backfilling_dividends",
                    stats["already_complete"] + stats["succeeded"],
                )
        if stats["failed"]:
            raise RuntimeError(
                f"Dividend history backfill failed for {stats['failed']} securities"
            )
        quality = {"passed": True, "metrics": stats}
        async with async_session_maker() as db, db.begin():
            await publish_datasets_and_finish(
                db,
                [DIVIDEND_HISTORY_DATASET],
                target,
                run_id,
                quality_report=quality,
                records_processed=stats["already_complete"] + stats["succeeded"],
            )
        return {
            "run_id": run_id,
            "status": "published",
            "as_of_date": target.isoformat(),
            **stats,
        }
    except asyncio.CancelledError:
        await finish_pipeline_run(
            run_id,
            "cancelled",
            quality_report={"metrics": stats},
            error_message="Dividend history backfill was cancelled",
        )
        raise
    except Exception as exc:
        await finish_pipeline_run(
            run_id,
            "failed",
            quality_report={"metrics": stats},
            error_message=str(exc),
        )
        raise


async def backfill_latest_screener_dividends_once(
    target_date: Optional[date] = None,
) -> dict:
    """Run the versioned dividend upgrade for the latest published screener universe."""
    snapshot_date = await latest_published_date("screener")
    if snapshot_date is None:
        return {"status": "deferred", "reason": "screener-not-published"}
    async with async_session_maker() as db:
        result = await db.execute(
            select(StockScreenerSnapshot.ticker).where(
                StockScreenerSnapshot.date == snapshot_date
            )
        )
        tickers = result.scalars().all()
    target = target_date or snapshot_date
    return await backfill_dividend_history_once(
        tickers,
        target_date=target,
        required_through_date=(
            target
            if has_unpublished_market_session_gap(snapshot_date, target)
            else None
        ),
    )


async def backfill_price_history(
    tickers: Iterable[str],
    history_days: Optional[int] = None,
    target_date: Optional[date] = None,
    include_corporate_actions: bool = True,
    include_dividends: bool = True,
) -> dict:
    symbols = sorted({ticker.upper() for ticker in tickers})
    days = history_days or settings.COLD_START_HISTORY_DAYS
    target = target_date or date.today()
    if symbols:
        async with async_session_maker() as security_db, security_db.begin():
            await security_db.execute(
                insert(Ticker)
                .values([{"ticker": ticker} for ticker in symbols])
                .on_conflict_do_nothing(index_elements=["ticker"])
            )
    calendar_start = target - timedelta(days=int(days * 1.8) + 30)
    dividend_start = target - timedelta(days=365 * 7)
    minimum_rows = max(1, int(days * 0.9))
    full_window_rows = days + 1
    latest_acceptable_date = latest_completed_us_session(target)
    expected_sessions: set[date] = set()
    session_cursor = latest_acceptable_date
    while len(expected_sessions) < full_window_rows:
        if is_us_market_session(session_cursor):
            expected_sessions.add(session_cursor)
        session_cursor -= timedelta(days=1)
    complete_symbols: set[str] = set()
    async with async_session_maker() as coverage_db:
        for start in range(0, len(symbols), 500):
            chunk = symbols[start:start + 500]
            coverage_result = await coverage_db.execute(
                select(DailyPrice.ticker, DailyPrice.date).where(
                    DailyPrice.ticker.in_(chunk),
                    DailyPrice.date.in_(expected_sessions),
                )
            )
            dates_by_ticker: dict[str, set[date]] = {}
            for ticker, price_date in coverage_result.all():
                dates_by_ticker.setdefault(ticker, set()).add(price_date)
            complete_symbols.update(
                ticker
                for ticker, price_dates in dates_by_ticker.items()
                if expected_sessions.issubset(price_dates)
            )
    symbols_to_process = (
        symbols
        if include_corporate_actions
        else [ticker for ticker in symbols if ticker not in complete_symbols]
    )
    if not symbols_to_process:
        return {
            "status": "skipped",
            "reason": "price-history-complete",
            "requested": len(symbols),
            "succeeded": 0,
            "skipped": len(symbols),
            "failed": 0,
            "price_rows": 0,
            "corporate_actions": 0,
            "ticker_coverage": 1.0,
        }
    run_id = await begin_pipeline_run("price_history_backfill", target)
    semaphore = asyncio.Semaphore(settings.HISTORY_BACKFILL_CONCURRENCY)
    progress_lock = asyncio.Lock()
    stats = {
        "requested": len(symbols),
        "succeeded": 0,
        "skipped": len(complete_symbols) if not include_corporate_actions else 0,
        "failed": 0,
        "price_rows": 0,
        "corporate_actions": 0,
    }

    async def process(ticker: str) -> None:
        async with semaphore:
            try:
                prices_complete = ticker in complete_symbols
                if prices_complete and not include_corporate_actions:
                    async with progress_lock:
                        stats["skipped"] += 1
                    return

                tasks = []
                if not prices_complete:
                    tasks.append(
                        eodhd_client.get_eod_historical_data(
                            ticker,
                            calendar_start.isoformat(),
                            target.isoformat(),
                            client=client,
                        )
                    )
                if include_corporate_actions:
                    tasks.append(
                        eodhd_client.get_splits(
                            ticker, calendar_start.isoformat(), target.isoformat(), client=client
                        )
                    )
                    if include_dividends:
                        tasks.append(
                            eodhd_client.get_dividends(
                                ticker,
                                dividend_start.isoformat(),
                                target.isoformat(),
                                client=client,
                            )
                        )
                responses = await asyncio.gather(*tasks)
                response_index = 0
                prices = []
                if not prices_complete:
                    prices = responses[response_index]
                    response_index += 1
                if include_corporate_actions:
                    splits = responses[response_index]
                    response_index += 1
                    if splits is None:
                        raise ValueError("provider failed to return splits")
                    if include_dividends:
                        dividends = responses[response_index]
                        if dividends is None:
                            raise ValueError("provider failed to return dividends")
                    else:
                        dividends = []
                else:
                    splits, dividends = [], []
                if not prices_complete and not prices:
                    raise ValueError("provider returned no price history")

                snapshot_identity = {
                    "ticker": ticker,
                    "from_date": calendar_start.isoformat(),
                    "to_date": target.isoformat(),
                }
                dividend_snapshot_identity = {
                    **snapshot_identity,
                    "from_date": dividend_start.isoformat(),
                }
                async with async_session_maker() as db, db.begin():
                    if prices:
                        await persist_snapshot(
                            db,
                            "EODHD",
                            "eod_prices",
                            prices,
                            as_of_date=target,
                            details=snapshot_identity,
                        )
                        await _upsert_daily_prices(ticker, prices, db)
                    action_count = await upsert_corporate_actions(db, ticker, splits or [], dividends or [])
                    if include_corporate_actions:
                        await persist_snapshot(
                            db,
                            "EODHD",
                            "splits",
                            splits or [],
                            as_of_date=target,
                            details=snapshot_identity,
                        )
                    if include_corporate_actions and include_dividends:
                        await persist_snapshot(
                            db,
                            "EODHD",
                            "dividends",
                            dividends or [],
                            as_of_date=target,
                            details=dividend_snapshot_identity,
                        )

                async with async_session_maker() as coverage_db:
                    final_coverage_result = await coverage_db.execute(
                        select(DailyPrice.date).where(
                            DailyPrice.ticker == ticker,
                            DailyPrice.date.in_(expected_sessions),
                        )
                    )
                    final_sessions = set(final_coverage_result.scalars())
                missing_sessions = expected_sessions - final_sessions
                if missing_sessions:
                    raise ValueError(
                        "price history coverage is incomplete: "
                        f"missing {len(missing_sessions)} of {full_window_rows} "
                        f"required market sessions through {latest_acceptable_date}"
                    )
                async with progress_lock:
                    stats["skipped" if prices_complete else "succeeded"] += 1
                    stats["price_rows"] += len(prices)
                    stats["corporate_actions"] += action_count
            except Exception as exc:
                logger.warning("History backfill failed for %s: %s", ticker, exc)
                async with progress_lock:
                    stats["failed"] += 1

    try:
        await update_pipeline_run(run_id, "backfilling", 0)
        async with eodhd_client.create_http_client() as client:
            for start in range(0, len(symbols_to_process), 250):
                await asyncio.gather(
                    *(
                        process(ticker)
                        for ticker in symbols_to_process[start:start + 250]
                    )
                )
                await update_pipeline_run(run_id, "backfilling", stats["succeeded"] + stats["skipped"])

        coverage = (stats["succeeded"] + stats["skipped"]) / len(symbols) if symbols else 0.0
        quality = {
            "passed": coverage >= 0.90,
            "metrics": {
                **stats,
                "ticker_coverage": coverage,
                "minimum_rows_per_ticker": minimum_rows,
                "full_window_rows": full_window_rows,
                "latest_acceptable_date": latest_acceptable_date.isoformat(),
            },
        }
        if not quality["passed"]:
            raise RuntimeError(f"History backfill coverage below 90%: {coverage:.2%}")
        async with async_session_maker() as publication_db, publication_db.begin():
            await publish_datasets_and_finish(
                publication_db,
                ["price_history"],
                target,
                run_id,
                quality_report=quality,
                records_processed=stats["succeeded"] + stats["skipped"],
            )
        return {"run_id": run_id, "status": "published", **quality["metrics"]}
    except asyncio.CancelledError:
        await finish_pipeline_run(
            run_id,
            "cancelled",
            quality_report={"metrics": stats},
            error_message="History backfill was cancelled",
        )
        raise
    except Exception as exc:
        await finish_pipeline_run(run_id, "failed", quality_report={"metrics": stats}, error_message=str(exc))
        raise
