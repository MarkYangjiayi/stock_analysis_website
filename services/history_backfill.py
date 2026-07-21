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
from services.pipeline_runs import begin_pipeline_run, finish_pipeline_run, publish_dataset, update_pipeline_run
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
    run_id = await begin_pipeline_run("price_history_backfill", target)
    semaphore = asyncio.Semaphore(settings.HISTORY_BACKFILL_CONCURRENCY)
    progress_lock = asyncio.Lock()
    stats = {"requested": len(symbols), "succeeded": 0, "skipped": 0, "failed": 0, "price_rows": 0, "corporate_actions": 0}

    async def process(ticker: str) -> None:
        async with semaphore:
            try:
                async with async_session_maker() as check_db:
                    count_result = await check_db.execute(
                        select(func.count(DailyPrice.id)).where(
                            DailyPrice.ticker == ticker,
                            DailyPrice.date >= calendar_start,
                            DailyPrice.date <= target,
                        )
                    )
                    existing_count = count_result.scalar_one()
                if existing_count >= int(days * 0.9):
                    async with progress_lock:
                        stats["skipped"] += 1
                    return

                price_task = eodhd_client.get_eod_historical_data(
                    ticker, calendar_start.isoformat(), target.isoformat(), client=client
                )
                if include_corporate_actions:
                    split_task = eodhd_client.get_splits(
                        ticker, calendar_start.isoformat(), target.isoformat(), client=client
                    )
                    dividend_task = eodhd_client.get_dividends(
                        ticker, calendar_start.isoformat(), target.isoformat(), client=client
                    )
                    prices, splits, dividends = await asyncio.gather(price_task, split_task, dividend_task)
                else:
                    prices = await price_task
                    splits, dividends = [], []
                if not prices:
                    raise ValueError("provider returned no price history")

                async with async_session_maker() as db, db.begin():
                    await persist_snapshot(
                        db, "EODHD", "eod_prices", prices, as_of_date=target, details={"ticker": ticker}
                    )
                    await _upsert_daily_prices(ticker, prices, db)
                    action_count = await upsert_corporate_actions(db, ticker, splits or [], dividends or [])
                    if include_corporate_actions:
                        await persist_snapshot(db, "EODHD", "splits", splits or [], as_of_date=target, details={"ticker": ticker})
                        await persist_snapshot(db, "EODHD", "dividends", dividends or [], as_of_date=target, details={"ticker": ticker})
                async with progress_lock:
                    stats["succeeded"] += 1
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
        quality = {"passed": coverage >= 0.90, "metrics": {**stats, "ticker_coverage": coverage}}
        if not quality["passed"]:
            raise RuntimeError(f"History backfill coverage below 90%: {coverage:.2%}")
        await publish_dataset("price_history", target, run_id)
        await finish_pipeline_run(run_id, "published", quality_report=quality)
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
