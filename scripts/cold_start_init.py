import asyncio
import logging
import sys
import os
from datetime import date

# Add the project root to python path to run from scripts/
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import init_db, async_session_maker
from models import StockScreenerSnapshot, Ticker
from sqlalchemy import select
from services.history_backfill import backfill_price_history
from services.screener_sync import refresh_screener_technicals, run_screener_pipeline
from core.config import settings
from services.quant.factor_engine import compute_and_store_factors
from services.pipeline_runs import latest_published_date
from services.rrg_prices import (
    RRG_PRICE_TICKERS,
    refresh_rrg_corporate_actions,
    refresh_rrg_price_history,
)
from core.trading_calendar import latest_completed_us_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

BASE_TICKERS = [
    "AAPL.US", "MSFT.US", *RRG_PRICE_TICKERS,
]

async def idempotent_seed_base_tickers():
    """保证幂等性的基础 Ticker 插入示例，确保被多次执行时不会引发重复"""

    async with async_session_maker() as db:
        async with db.begin():
            # 先批量查询数据库中已存在的 ticker
            existing_result = await db.execute(select(Ticker.ticker).where(Ticker.ticker.in_(BASE_TICKERS)))
            existing_tickers = set(row[0] for row in existing_result.all())
            
            new_tickers = [t for t in BASE_TICKERS if t not in existing_tickers]
            if new_tickers:
                logger.info(f"Idempotent Init: Found {len(new_tickers)} new base tickers. Inserting...")
                for t in new_tickers:
                    # 使用 session.merge() (Upsert 逻辑) 来覆盖或插入，避免无脑新增
                    new_ticker_obj = Ticker(ticker=t, name=f"Base Seed {t}")
                    await db.merge(new_ticker_obj)
            else:
                logger.info("Idempotent Init: Base tickers already exist. Skipping duplicate insertion.")

async def cold_start():
    logger.info("=== Starting Cold Init for QuantDashboard ===")
    
    logger.info("Step 1: Initializing empty database tables...")
    await init_db()
    logger.info("Tables created successfully.")
    
    logger.info("Step 1.5: Running Idempotent Base Seeding...")
    await idempotent_seed_base_tickers()
    
    expected_session = latest_completed_us_session(date.today())
    published_screener = await latest_published_date("screener")
    if published_screener is not None and published_screener >= expected_session:
        snapshot_date = published_screener
        logger.info("Step 2: Reusing published Screener snapshot %s.", snapshot_date)
    else:
        logger.info("Step 2: Launching Full Market Screener Sync (This may take 3-5 minutes)...")
        logger.info("This step fetches >2400 index constituents, prices, and fundamentals.")
        screener_result = await run_screener_pipeline(target_date=None)
        snapshot_date = date.fromisoformat(screener_result["as_of_date"])
    async with async_session_maker() as db:
        result = await db.execute(
            select(StockScreenerSnapshot.ticker).where(StockScreenerSnapshot.date == snapshot_date)
        )
        universe_tickers = set(result.scalars().all())
    universe_tickers.update(BASE_TICKERS)
    general_history_tickers = universe_tickers.difference(RRG_PRICE_TICKERS)

    logger.info(
        "Step 3: Backfilling %s trading days for %s securities...",
        settings.COLD_START_HISTORY_DAYS,
        len(general_history_tickers),
    )
    await backfill_price_history(
        general_history_tickers,
        target_date=snapshot_date,
        include_dividends=False,
        publish_dataset=False,
    )

    logger.info("Step 3.5: Ensuring the RRG ETF universe has its extra warm-up window...")
    await refresh_rrg_price_history(snapshot_date)
    try:
        await refresh_rrg_corporate_actions(snapshot_date)
    except Exception as exc:
        logger.warning("RRG split history could not be completed: %s", exc)

    logger.info("Step 4: Recomputing technical indicators after price warm-up...")
    updated = await refresh_screener_technicals(snapshot_date)
    logger.info("Updated technical indicators for %s snapshot rows.", updated)

    logger.info("Step 5: Computing the first versioned factor cross-section...")
    published_factors = await latest_published_date("factors")
    if published_factors is not None and published_factors >= snapshot_date:
        logger.info("Factor snapshot %s is already published; skipping.", published_factors)
    else:
        async with async_session_maker() as db:
            await compute_and_store_factors(db, snapshot_date)
    
    logger.info("=== Cold Start Complete! You can now start the web server. ===")

if __name__ == "__main__":
    asyncio.run(cold_start())
