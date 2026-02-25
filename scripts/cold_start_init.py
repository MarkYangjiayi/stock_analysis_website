import asyncio
import logging
import sys
import os

# Add the project root to python path to run from scripts/
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import init_db
from services.screener_sync import run_screener_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

async def cold_start():
    logger.info("=== Starting Cold Init for QuantDashboard ===")
    
    logger.info("Step 1: Initializing empty database tables...")
    await init_db()
    logger.info("Tables created successfully.")
    
    logger.info("Step 2: Launching Full Market Screener Sync (This may take 3-5 minutes)...")
    logger.info("This step fetches >2400 index constituents, prices, and fundamentals.")
    await run_screener_pipeline(target_date=None)
    
    logger.info("=== Cold Start Complete! You can now start the web server. ===")

if __name__ == "__main__":
    asyncio.run(cold_start())
