import asyncio
import logging
import sys
import os

# Add the project root to python path to run from scripts/
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import init_db, async_session_maker
from models import Ticker
from sqlalchemy import select
from services.screener_sync import run_screener_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

async def idempotent_seed_base_tickers():
    """保证幂等性的基础 Ticker 插入示例，确保被多次执行时不会引发重复"""
    seed_tickers = ["AAPL.US", "MSFT.US", "SPY"]
    
    async with async_session_maker() as db:
        async with db.begin():
            # 先批量查询数据库中已存在的 ticker
            existing_result = await db.execute(select(Ticker.ticker).where(Ticker.ticker.in_(seed_tickers)))
            existing_tickers = set(row[0] for row in existing_result.all())
            
            new_tickers = [t for t in seed_tickers if t not in existing_tickers]
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
    
    logger.info("Step 2: Launching Full Market Screener Sync (This may take 3-5 minutes)...")
    logger.info("This step fetches >2400 index constituents, prices, and fundamentals.")
    await run_screener_pipeline(target_date=None)
    
    logger.info("=== Cold Start Complete! You can now start the web server. ===")

if __name__ == "__main__":
    asyncio.run(cold_start())
