import asyncio
import logging
from sqlalchemy import select, delete
from database import async_session_maker
from models import Ticker, DailyPrice, FinancialStatement, StockScreenerSnapshot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _mutual_fund_ticker(column):
    """Match exactly five uppercase letters ending in X, plus the .US suffix."""
    return column.op("GLOB")("[A-Z][A-Z][A-Z][A-Z]X.US")


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
            _mutual_fund_ticker(StockScreenerSnapshot.ticker)
        )
        res_snap = await db.execute(stmt_snap)
        logger.info(f"Deleted {res_snap.rowcount} Mutual Fund records from stock_screener_snapshot.")
        
        # 2. Clean DailyPrice table (Child of Ticker)
        stmt_prices = delete(DailyPrice).where(
            _mutual_fund_ticker(DailyPrice.ticker)
        )
        res_prices = await db.execute(stmt_prices)
        logger.info(f"Deleted {res_prices.rowcount} Mutual Fund records from daily_prices.")
        
        # 3. Clean FinancialStatement table (Child of Ticker)
        stmt_financials = delete(FinancialStatement).where(
            _mutual_fund_ticker(FinancialStatement.ticker)
        )
        res_financials = await db.execute(stmt_financials)
        logger.info(f"Deleted {res_financials.rowcount} Mutual Fund records from financial_statements.")
        
        # 4. Clean Ticker table (Parent)
        stmt_ticker = delete(Ticker).where(
            _mutual_fund_ticker(Ticker.ticker)
        )
        res_ticker = await db.execute(stmt_ticker)
        logger.info(f"Deleted {res_ticker.rowcount} Mutual Fund definitions from tickers table.")

if __name__ == "__main__":
    asyncio.run(clean_database())
