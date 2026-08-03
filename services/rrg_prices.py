"""Versioned price-history maintenance for the sector rotation dashboard."""

import asyncio
import math
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import delete, desc, func, select
from sqlalchemy.dialects.sqlite import insert

from core.config import settings
from core.trading_calendar import is_us_market_session, latest_completed_us_session
from database import async_session_maker
from models import (
    DataPublication,
    PipelineRun,
    RRGPriceSnapshot,
    Ticker,
)
from services import eodhd_client
from services.data_sync import _upsert_daily_prices
from services.history_backfill import backfill_price_history
from services.pipeline_runs import (
    begin_pipeline_run,
    finish_pipeline_run,
    publish_datasets_and_finish,
    update_pipeline_run,
)


RRG_BENCHMARK = "SPY.US"
RRG_SECTOR_NAMES = {
    "XLK.US": "Technology",
    "XLF.US": "Financials",
    "XLV.US": "Health Care",
    "XLY.US": "Cons. Discret.",
    "XLP.US": "Cons. Staples",
    "XLE.US": "Energy",
    "XLI.US": "Industrials",
    "XLB.US": "Materials",
    "XLU.US": "Utilities",
    "XLRE.US": "Real Estate",
    "XLC.US": "Comm. Svcs",
}
RRG_AUXILIARY_TICKERS = ("RSP.US",)
RRG_HISTORY_DAYS = 252
RRG_WARMUP_DAYS = 100
RRG_PRICE_HISTORY_DAYS = RRG_HISTORY_DAYS + RRG_WARMUP_DAYS
RRG_PRICE_TICKERS = (*RRG_SECTOR_NAMES, *RRG_AUXILIARY_TICKERS, RRG_BENCHMARK)
RRG_PRICE_HISTORY_DATASET = "rrg_price_history"
RRG_CORPORATE_ACTIONS_DATASET = "rrg_corporate_actions"
RRG_SNAPSHOT_RETENTION_RUNS = 5

_rrg_refresh_lock = None
_rrg_refresh_lock_loop = None


def _current_refresh_lock() -> asyncio.Lock:
    global _rrg_refresh_lock, _rrg_refresh_lock_loop
    loop = asyncio.get_running_loop()
    if _rrg_refresh_lock is None or _rrg_refresh_lock_loop is not loop:
        _rrg_refresh_lock = asyncio.Lock()
        _rrg_refresh_lock_loop = loop
    return _rrg_refresh_lock


def _normalize_rrg_prices(
    rows: list,
    expected_sessions: set[date],
) -> list[dict]:
    normalized_by_date = {}
    for row in rows or []:
        try:
            price_date = date.fromisoformat(str(row.get("date")))
        except (AttributeError, TypeError, ValueError):
            continue
        if price_date not in expected_sessions:
            continue

        adjusted_close = row.get("adjusted_close")
        raw_close = adjusted_close if adjusted_close is not None else row.get("close")
        try:
            effective_close = float(raw_close)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(effective_close) or effective_close <= 0:
            continue

        normalized_by_date[price_date] = {
            **row,
            "date": price_date.isoformat(),
            "_effective_close": effective_close,
        }
    return [
        normalized_by_date[price_date]
        for price_date in sorted(normalized_by_date)
    ]


async def _latest_complete_publication(
    expected_rows: int,
) -> Optional[date]:
    async with async_session_maker() as db:
        result = await db.execute(
            select(DataPublication)
            .join(PipelineRun, PipelineRun.id == DataPublication.pipeline_run_id)
            .where(
                DataPublication.dataset == RRG_PRICE_HISTORY_DATASET,
                DataPublication.status == "published",
                PipelineRun.status == "published",
            )
            .order_by(desc(DataPublication.as_of_date))
            .limit(1)
        )
        publication = result.scalar_one_or_none()
        if publication is None:
            return None
        snapshot_count = await db.scalar(
            select(func.count(RRGPriceSnapshot.id)).where(
                RRGPriceSnapshot.pipeline_run_id == publication.pipeline_run_id
            )
        )
        if snapshot_count != expected_rows:
            return None
        return publication.as_of_date


async def refresh_rrg_price_history(target_date: date) -> dict:
    """Serialize and atomically publish a validated RRG price snapshot."""
    async with _current_refresh_lock():
        return await _refresh_rrg_price_history_locked(target_date)


async def _refresh_rrg_price_history_locked(target_date: date) -> dict:
    target = (
        target_date
        if is_us_market_session(target_date)
        else latest_completed_us_session(target_date)
    )
    expected_row_count = (
        len(RRG_PRICE_TICKERS) * (RRG_PRICE_HISTORY_DAYS + 1)
    )
    published = await _latest_complete_publication(expected_row_count)
    if published is not None and published >= target:
        return {
            "status": "skipped",
            "reason": "already-published",
            "as_of_date": published.isoformat(),
        }

    expected_sessions: set[date] = set()
    cursor = target
    while len(expected_sessions) < RRG_PRICE_HISTORY_DAYS + 1:
        if is_us_market_session(cursor):
            expected_sessions.add(cursor)
        cursor -= timedelta(days=1)
    calendar_start = target - timedelta(
        days=int(RRG_PRICE_HISTORY_DAYS * 1.8) + 30
    )

    run_id = await begin_pipeline_run(
        f"{RRG_PRICE_HISTORY_DATASET}_backfill",
        target,
    )
    semaphore = asyncio.Semaphore(settings.HISTORY_BACKFILL_CONCURRENCY)

    async def fetch_one(ticker: str, client) -> tuple[str, list[dict]]:
        async with semaphore:
            raw_rows = await eodhd_client.get_eod_historical_data(
                ticker,
                calendar_start.isoformat(),
                target.isoformat(),
                client=client,
            )
            if not raw_rows:
                raise ValueError(f"{ticker}: provider returned no price history")
            normalized = _normalize_rrg_prices(raw_rows, expected_sessions)
            covered_dates = {
                date.fromisoformat(row["date"])
                for row in normalized
            }
            missing = expected_sessions - covered_dates
            if missing:
                raise ValueError(
                    f"{ticker}: missing {len(missing)} of "
                    f"{len(expected_sessions)} required sessions through {target}"
                )
            return ticker, normalized

    try:
        await update_pipeline_run(run_id, "fetching", 0)
        async with eodhd_client.create_http_client() as client:
            fetched = await asyncio.gather(
                *(fetch_one(ticker, client) for ticker in RRG_PRICE_TICKERS),
                return_exceptions=True,
            )
        failures = [item for item in fetched if isinstance(item, BaseException)]
        if failures:
            raise RuntimeError(
                "RRG price refresh failed: "
                + "; ".join(str(failure) for failure in failures)
            )

        results = dict(fetched)
        snapshot_rows = []
        await update_pipeline_run(run_id, "publishing", 0)
        async with async_session_maker() as db, db.begin():
            await db.execute(
                insert(Ticker)
                .values([{"ticker": ticker} for ticker in RRG_PRICE_TICKERS])
                .on_conflict_do_nothing(index_elements=["ticker"])
            )
            for ticker, normalized in results.items():
                clean_prices = [
                    {
                        key: value
                        for key, value in row.items()
                        if key != "_effective_close"
                    }
                    for row in normalized
                ]
                await _upsert_daily_prices(ticker, clean_prices, db)
                snapshot_rows.extend(
                    {
                        "pipeline_run_id": run_id,
                        "ticker": ticker,
                        "date": date.fromisoformat(row["date"]),
                        "close": row["_effective_close"],
                    }
                    for row in normalized
                )
            for start in range(0, len(snapshot_rows), 200):
                await db.execute(
                    insert(RRGPriceSnapshot).values(
                        snapshot_rows[start:start + 200]
                    )
                )
            quality = {
                "passed": True,
                "metrics": {
                    "requested": len(RRG_PRICE_TICKERS),
                    "succeeded": len(RRG_PRICE_TICKERS),
                    "failed": 0,
                    "sessions_per_ticker": len(expected_sessions),
                    "snapshot_rows": len(snapshot_rows),
                    "latest_acceptable_date": target.isoformat(),
                },
            }
            await publish_datasets_and_finish(
                db,
                [RRG_PRICE_HISTORY_DATASET],
                target,
                run_id,
                quality_report=quality,
                records_processed=len(snapshot_rows),
            )
            protected_breadth_dates = (
                select(DataPublication.as_of_date)
                .join(PipelineRun, PipelineRun.id == DataPublication.pipeline_run_id)
                .where(
                    DataPublication.dataset == "market_breadth",
                    DataPublication.status == "published",
                    PipelineRun.status == "published",
                )
            )
            expired_publications_result = await db.execute(
                select(DataPublication)
                .where(
                    DataPublication.dataset == RRG_PRICE_HISTORY_DATASET,
                    DataPublication.as_of_date.not_in(protected_breadth_dates),
                )
                .order_by(desc(DataPublication.as_of_date))
                .offset(RRG_SNAPSHOT_RETENTION_RUNS)
            )
            expired_publications = list(expired_publications_result.scalars())
            expired_run_ids = [
                publication.pipeline_run_id
                for publication in expired_publications
            ]
            if expired_run_ids:
                await db.execute(
                    delete(RRGPriceSnapshot).where(
                        RRGPriceSnapshot.pipeline_run_id.in_(expired_run_ids)
                    )
                )
                for publication in expired_publications:
                    await db.delete(publication)
            retained_run_ids = list((await db.execute(
                select(DataPublication.pipeline_run_id).where(
                    DataPublication.dataset == RRG_PRICE_HISTORY_DATASET
                )
            )).scalars())
            await db.execute(
                delete(RRGPriceSnapshot).where(
                    RRGPriceSnapshot.pipeline_run_id.not_in(retained_run_ids)
                )
            )
        return {
            "run_id": run_id,
            "status": "published",
            "as_of_date": target.isoformat(),
            **quality["metrics"],
        }
    except asyncio.CancelledError:
        await finish_pipeline_run(run_id, "cancelled")
        raise
    except Exception as exc:
        await finish_pipeline_run(
            run_id,
            "failed",
            error_message=str(exc),
        )
        raise


async def refresh_rrg_corporate_actions(target_date: date) -> dict:
    """Best-effort split maintenance, isolated from RRG price publication."""
    return await backfill_price_history(
        RRG_PRICE_TICKERS,
        history_days=RRG_PRICE_HISTORY_DAYS,
        target_date=target_date,
        include_corporate_actions=True,
        include_dividends=False,
        publication_dataset=RRG_CORPORATE_ACTIONS_DATASET,
        minimum_ticker_coverage=0.90,
        include_target_session=True,
    )
