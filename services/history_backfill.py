import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy import func, select

from core.config import settings
from database import async_session_maker
from models import DailyPrice
from services import eodhd_client
from services.corporate_actions import upsert_corporate_actions
from services.data_sync import _upsert_daily_prices
from services.pipeline_runs import (
    begin_pipeline_run,
    finish_pipeline_run,
    publish_datasets_and_finish,
    update_pipeline_run,
)
from services.raw_store import persist_snapshot


logger = logging.getLogger(__name__)


async def backfill_price_history(
    tickers: Iterable[str],
    history_days: Optional[int] = None,
    target_date: Optional[date] = None,
    include_corporate_actions: bool = True,
) -> dict:
    symbols = sorted({ticker.upper() for ticker in tickers})
    days = history_days or settings.COLD_START_HISTORY_DAYS
    target = target_date or date.today()
    calendar_start = target - timedelta(days=int(days * 1.8) + 30)
    dividend_start = target - timedelta(days=365 * 7)
    minimum_rows = max(1, int(days * 0.9))
    latest_acceptable_date = target - timedelta(days=7)
    run_id = await begin_pipeline_run("price_history_backfill", target)
    semaphore = asyncio.Semaphore(settings.HISTORY_BACKFILL_CONCURRENCY)
    progress_lock = asyncio.Lock()
    stats = {"requested": len(symbols), "succeeded": 0, "skipped": 0, "failed": 0, "price_rows": 0, "corporate_actions": 0}

    async def process(ticker: str) -> None:
        async with semaphore:
            try:
                async with async_session_maker() as check_db:
                    coverage_result = await check_db.execute(
                        select(func.count(DailyPrice.id), func.max(DailyPrice.date)).where(
                            DailyPrice.ticker == ticker,
                            DailyPrice.date >= calendar_start,
                            DailyPrice.date <= target,
                        )
                    )
                    existing_count, existing_latest = coverage_result.one()
                prices_complete = (
                    existing_count >= minimum_rows
                    and existing_latest is not None
                    and existing_latest >= latest_acceptable_date
                )
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
                    tasks.extend([
                        eodhd_client.get_splits(
                            ticker, calendar_start.isoformat(), target.isoformat(), client=client
                        ),
                        eodhd_client.get_dividends(
                            ticker, dividend_start.isoformat(), target.isoformat(), client=client
                        ),
                    ])
                responses = await asyncio.gather(*tasks)
                response_index = 0
                prices = []
                if not prices_complete:
                    prices = responses[response_index]
                    response_index += 1
                if include_corporate_actions:
                    splits = responses[response_index]
                    dividends = responses[response_index + 1]
                    if splits is None or dividends is None:
                        raise ValueError("provider failed to return corporate actions")
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
                        select(func.count(DailyPrice.id), func.max(DailyPrice.date)).where(
                            DailyPrice.ticker == ticker,
                            DailyPrice.date >= calendar_start,
                            DailyPrice.date <= target,
                        )
                    )
                    final_count, final_latest = final_coverage_result.one()
                if (
                    final_count < minimum_rows
                    or final_latest is None
                    or final_latest < latest_acceptable_date
                ):
                    raise ValueError(
                        "price history coverage is incomplete: "
                        f"rows={final_count}/{minimum_rows}, latest={final_latest}, "
                        f"required_latest={latest_acceptable_date}"
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
            for start in range(0, len(symbols), 250):
                await asyncio.gather(*(process(ticker) for ticker in symbols[start:start + 250]))
                await update_pipeline_run(run_id, "backfilling", stats["succeeded"] + stats["skipped"])

        coverage = (stats["succeeded"] + stats["skipped"]) / len(symbols) if symbols else 0.0
        quality = {
            "passed": coverage >= 0.90,
            "metrics": {
                **stats,
                "ticker_coverage": coverage,
                "minimum_rows_per_ticker": minimum_rows,
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
