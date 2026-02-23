import asyncio
import logging
from sqlalchemy import select, delete
from database import async_session_maker
from models import Ticker, DailyPrice, FinancialStatement, StockScreenerSnapshot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def clean_database():
    """
    Remove all Mutual Funds (5-letter tickers ending in 'X') and OTC stocks from the database.
    Because Ticker is tied via cascade rules to DailyPrice and FinancialStatement, 
    deleting from Ticker will automatically clean those tables.
    StockScreenerSnapshot is independent and must be deleted separately.
    """
    async with async_session_maker() as db, db.begin():
        # Find all tickers ending in 'X.US' with length 8 (e.g. AEGFX.US)
        # Or those with specific OTC exchanges if we had them (not currently tracked in snapshot)
        
        logger.info("Identifying Mutual Funds to purge...")
        
        # 1. Clean StockScreenerSnapshot table
        stmt_snap = delete(StockScreenerSnapshot).where(
            StockScreenerSnapshot.ticker.op('~')('^[A-Z]{4}X\.US$')
        )
        res_snap = await db.execute(stmt_snap)
        logger.info(f"Deleted {res_snap.rowcount} Mutual Fund records from stock_screener_snapshot.")
        
        # 2. Clean DailyPrice table (Child of Ticker)
        stmt_prices = delete(DailyPrice).where(
            DailyPrice.ticker.op('~')('^[A-Z]{4}X\.US$')
        )
        res_prices = await db.execute(stmt_prices)
        logger.info(f"Deleted {res_prices.rowcount} Mutual Fund records from daily_prices.")
        
        # 3. Clean FinancialStatement table (Child of Ticker)
        stmt_financials = delete(FinancialStatement).where(
            FinancialStatement.ticker.op('~')('^[A-Z]{4}X\.US$')
        )
        res_financials = await db.execute(stmt_financials)
        logger.info(f"Deleted {res_financials.rowcount} Mutual Fund records from financial_statements.")
        
        # 4. Clean Ticker table (Parent)
        stmt_ticker = delete(Ticker).where(
            Ticker.ticker.op('~')('^[A-Z]{4}X\.US$')
        )
        res_ticker = await db.execute(stmt_ticker)
        logger.info(f"Deleted {res_ticker.rowcount} Mutual Fund definitions from tickers table.")

if __name__ == "__main__":
    asyncio.run(clean_database())
