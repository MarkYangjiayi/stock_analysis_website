import asyncio
import logging
import sys
import os

from sqlalchemy.ext.asyncio import AsyncSession

# Add the project root to the path so we can import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import async_session_maker
from services.data_sync import sync_ticker_data

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

async def main():
    tickers = ["SPY", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL"]
    
    logger.info("Starting synchronization of RRG data for tickers: %s", tickers)
    
    async with async_session_maker() as session:
        for ticker in tickers:
            logger.info(f"Syncing data for {ticker}...")
            success = await sync_ticker_data(ticker, session)
            if success:
                logger.info(f"Successfully synced {ticker}.")
            else:
                logger.error(f"Failed to sync {ticker}.")
                
    logger.info("RRG data synchronization script completed.")

if __name__ == "__main__":
    asyncio.run(main())
