import asyncio
import logging
import sys
import os

# Add the project root to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import async_session_maker
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

async def cleanup_duplicates():
    """
    清理数据库中由于云端多次执行或缺失约束而产生的重复数据。
    保留每组相同的 ticker/date 组合中最小 id（或rowid）的记录。
    """
    logger.info("Connecting to database to clean up duplicate entries...")
    
    async with async_session_maker() as session:
        async with session.begin():
            # 1. 针对 tickers 表
            # 默认 SQLite 均支持 rowid 隐式主键，即使我们定义的 ticker 是 PK
            logger.info("Cleaning up 'tickers' table...")
            await session.execute(text("""
                DELETE FROM tickers 
                WHERE rowid NOT IN (
                    SELECT MIN(rowid)
                    FROM tickers
                    GROUP BY ticker
                );
            """))

            # 2. 针对 stock_screener_snapshot 表
            # 存在相同的 ticker + date 组合时，保留最小 ID 的那一条记录
            logger.info("Cleaning up 'stock_screener_snapshot' table...")
            await session.execute(text("""
                DELETE FROM stock_screener_snapshot 
                WHERE id NOT IN (
                    SELECT MIN(id)
                    FROM stock_screener_snapshot
                    GROUP BY ticker, date
                );
            """))

            # 3. 针对 daily_prices 表
            logger.info("Cleaning up 'daily_prices' table...")
            await session.execute(text("""
                DELETE FROM daily_prices 
                WHERE id NOT IN (
                    SELECT MIN(id)
                    FROM daily_prices
                    GROUP BY ticker, date
                );
            """))

        logger.info("Duplicate cleanup completed successfully. Only unique constraints remain!")

if __name__ == "__main__":
    asyncio.run(cleanup_duplicates())
