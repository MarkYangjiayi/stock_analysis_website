import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz
from datetime import datetime, timedelta

from services.daily_reporter import generate_morning_briefing, generate_post_market_summary

logger = logging.getLogger(__name__)

# Force strictly New York timezone to avoid DST issues
ny_tz = pytz.timezone('America/New_York')

scheduler = AsyncIOScheduler(timezone=ny_tz)

def start_scheduler():
    """Starts the global APScheduler instance and registers daily jobs."""
    logger.info("Starting APScheduler for Notifications...")
    
    # 1. Morning Briefing: Mon-Fri 09:35 EST
    scheduler.add_job(
        generate_morning_briefing,
        'cron',
        day_of_week='mon-fri',
        hour=9,
        minute=35,
        id="morning_briefing",
        replace_existing=True
    )
    
    # 2. Post Market Summary: Mon-Fri 16:05 EST
    scheduler.add_job(
        generate_post_market_summary,
        'cron',
        day_of_week='mon-fri',
        hour=16,
        minute=5,
        id="post_market_summary",
        replace_existing=True
    )
    
    # 3. [DEBUG] Execute Morning Briefing automatically 15 seconds after boot
    debug_run_time = datetime.now(ny_tz) + timedelta(seconds=15)
    scheduler.add_job(
        generate_morning_briefing,
        'date',
        run_date=debug_run_time,
        id="debug_morning_briefing",
        replace_existing=True
    )
    
    scheduler.start()

def shutdown_scheduler():
    """Safely shuts down the scheduler."""
    logger.info("Shutting down APScheduler...")
    scheduler.shutdown(wait=False)
