import asyncio
import logging
import sys
import os
from datetime import date

# Add the project root to python path to run from scripts/
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import init_db, async_session_maker
from models import Ticker
from sqlalchemy import select
from services.screener_sync import refresh_screener_technicals, run_screener_pipeline
from services.quant.factor_engine import compute_and_store_factors
from services.pipeline_runs import latest_published_date
from services.rrg_prices import (
    RRG_PRICE_TICKERS,
    refresh_rrg_corporate_actions,
    refresh_rrg_price_history,
)
from core.trading_calendar import latest_completed_us_session
from services.market_breadth import (
    backfill_market_breadth_price_history,
    refresh_market_breadth,
)
from services.universe import refresh_historical_universe_memberships

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
    else:
        snapshot_date = expected_session

    logger.info("Step 2: Importing strict point-in-time index membership history...")
    await refresh_historical_universe_memberships(snapshot_date)

    logger.info("Step 2.5: Backfilling the 504-session market breadth window...")
    await backfill_market_breadth_price_history(snapshot_date)

    logger.info("Step 3: Ensuring the ETF and RSP universe has its warm-up window...")
    await refresh_rrg_price_history(snapshot_date)
    try:
        await refresh_rrg_corporate_actions(snapshot_date)
    except Exception as exc:
        logger.warning("RRG split history could not be completed: %s", exc)

    if published_screener is not None and published_screener >= expected_session:
        logger.info("Step 3.5: Reusing published Screener snapshot %s.", snapshot_date)
    else:
        logger.info("Step 3.5: Launching Full Market Screener Sync (This may take 3-5 minutes)...")
        logger.info("This step fetches >2400 index constituents, prices, and fundamentals.")
        screener_result = await run_screener_pipeline(
            target_date=snapshot_date.isoformat(),
            observe_current_universe=True,
        )
        snapshot_date = date.fromisoformat(screener_result["as_of_date"])

    logger.info("Step 3.75: Publishing the first point-in-time market breadth panel...")
    await refresh_market_breadth(snapshot_date)

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
