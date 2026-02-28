import asyncio
import logging
import sys
import os

from sqlalchemy.ext.asyncio import AsyncSession

# Add the project root to the path so we can import modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database import async_session_maker
from services.data_sync import sync_ticker_data

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

async def main():
    # 包含了基准标的 SPY 以及 美股11大行业板块 ETF
    tickers = [
        "SPY",      # S&P 500 Benchmark
        "XLK.US",   # Technology
        "XLF.US",   # Financials
        "XLV.US",   # Health Care
        "XLY.US",   # Consumer Discretionary
        "XLP.US",   # Consumer Staples
        "XLE.US",   # Energy
        "XLI.US",   # Industrials
        "XLB.US",   # Materials
        "XLU.US",   # Utilities
        "XLRE.US",  # Real Estate
        "XLC.US"    # Communication Services
    ]
    
    logger.info("Starting synchronization of Sector ETF data for RRG analysis...")
    
    async with async_session_maker() as session:
        for ticker in tickers:
            logger.info(f"Syncing data for Sector/Benchmark {ticker}...")
            # sync_ticker_data internally grabs 120+ days of EOD historical data from EODHD
            success = await sync_ticker_data(ticker, session)
            if success:
                logger.info(f"Successfully synced {ticker}.")
            else:
                logger.error(f"Failed to sync {ticker}.")
                
    logger.info("Sector ETF data synchronization script completed.")

if __name__ == "__main__":
    asyncio.run(main())
