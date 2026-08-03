import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz
from datetime import date, datetime

from services.daily_reporter import generate_morning_briefing, generate_post_market_summary
from services.screener_sync import run_screener_pipeline
from services.quant.factor_engine import compute_factors_for_date
from scripts.backup_sqlite import create_backup
from core.trading_calendar import is_us_market_session, latest_completed_us_session
from services.pipeline_runs import latest_published_date
from services.rrg_prices import refresh_rrg_price_history
from services.market_breadth import refresh_market_breadth
from services.universe import refresh_historical_universe_memberships

logger = logging.getLogger(__name__)

# Force strictly New York timezone to avoid DST issues
ny_tz = pytz.timezone('America/New_York')

scheduler = AsyncIOScheduler(
    timezone=ny_tz,
    job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
)
_factor_sync_lock = asyncio.Lock()
_market_breadth_sync_lock = asyncio.Lock()


async def _sync_factors_for_target(target: date):
    """Publish factors only after the matching screener date is available."""
    async with _factor_sync_lock:
        published = await latest_published_date("factors")
        if published is not None and published >= target:
            return {"status": "skipped", "reason": "already-published", "as_of_date": target.isoformat()}

        screener_published = await latest_published_date("screener")
        if screener_published is None or screener_published < target:
            logger.info("Deferring factors for %s: matching screener is not published", target)
            return {"status": "deferred", "reason": "screener-not-published", "as_of_date": target.isoformat()}
        return await compute_factors_for_date(target)


async def scheduled_screener_sync(reference_date: date = None):
    target = latest_completed_us_session(reference_date or datetime.now(ny_tz).date())
    published = await latest_published_date("screener")
    if published is not None and published >= target:
        logger.info("Skipping screener sync: %s is already published", target)
        return {"status": "skipped", "reason": "already-published", "as_of_date": target.isoformat()}
    result = await run_screener_pipeline(
        target_date=target.isoformat(),
        observe_current_universe=True,
    )
    if result.get("status") == "published":
        try:
            breadth_result = await _sync_market_breadth_for_target(target)
        except Exception as exc:
            logger.exception("Market breadth post-screener attempt failed for %s: %s", target, exc)
            breadth_result = {
                "status": "failed",
                "as_of_date": target.isoformat(),
                "reason": str(exc),
            }
        result = {
            **result,
            "market_breadth": breadth_result,
            "factors": await _sync_factors_for_target(target),
        }
    return result


async def scheduled_factor_sync(reference_date: date = None):
    target = latest_completed_us_session(reference_date or datetime.now(ny_tz).date())
    return await _sync_factors_for_target(target)


async def scheduled_rrg_sync(reference_date: date = None):
    target = latest_completed_us_session(reference_date or datetime.now(ny_tz).date())
    return await refresh_rrg_price_history(target)


async def scheduled_universe_history_sync(reference_date: date = None):
    target = latest_completed_us_session(reference_date or datetime.now(ny_tz).date())
    return await refresh_historical_universe_memberships(target)


async def _sync_market_breadth_for_target(target: date):
    async with _market_breadth_sync_lock:
        published = await latest_published_date("market_breadth")
        if published is not None and published >= target:
            return {
                "status": "skipped",
                "reason": "already-published",
                "as_of_date": target.isoformat(),
            }
        return await refresh_market_breadth(target)


async def scheduled_market_breadth_sync(reference_date: date = None):
    target = latest_completed_us_session(reference_date or datetime.now(ny_tz).date())
    return await _sync_market_breadth_for_target(target)


async def scheduled_morning_briefing():
    if is_us_market_session(datetime.now(ny_tz).date()):
        return await generate_morning_briefing()
    return None


async def scheduled_post_market_summary():
    if is_us_market_session(datetime.now(ny_tz).date()):
        return await generate_post_market_summary()
    return None

def start_scheduler():
    """Starts the global APScheduler instance and registers daily jobs."""
    logger.info("Starting APScheduler for Notifications and Data Sync...")
    
    # 0. RRG prices: refresh the small ETF universe before the heavier screener job.
    scheduler.add_job(
        scheduled_rrg_sync,
        'cron',
        day_of_week='tue-sat',
        hour=1,
        minute=30,
        id="daily_rrg_price_sync",
        replace_existing=True,
    )
    scheduler.add_job(
        scheduled_universe_history_sync,
        'cron',
        day_of_week='tue-sat',
        hour=1,
        minute=45,
        id="daily_universe_history_sync",
        replace_existing=True,
    )
    # 1. Daily Screener Sync: Tue-Sat 02:00 AM EST (Fetches data after market close from Mon-Fri)
    scheduler.add_job(
        scheduled_screener_sync,
        'cron',
        day_of_week='tue-sat',
        hour=2,
        minute=0,
        id="daily_screener_sync",
        replace_existing=True
    )
    scheduler.add_job(
        scheduled_market_breadth_sync,
        'cron',
        day_of_week='tue-sat',
        hour=3,
        minute=30,
        id="daily_market_breadth_sync",
        replace_existing=True,
    )
    scheduler.add_job(
        scheduled_factor_sync,
        'cron',
        day_of_week='tue-sat',
        hour=4,
        minute=0,
        id="daily_factor_cross_section",
        replace_existing=True,
    )
    scheduler.add_job(
        create_backup,
        'cron',
        day_of_week='sun',
        hour=3,
        minute=0,
        id="weekly_sqlite_backup",
        replace_existing=True,
    )
    # 2. Morning Briefing: Mon-Fri 09:35 EST
    scheduler.add_job(
        scheduled_morning_briefing,
        'cron',
        day_of_week='mon-fri',
        hour=9,
        minute=35,
        id="morning_briefing",
        replace_existing=True
    )
    
    # 3. Post Market Summary: Mon-Fri 16:05 EST
    scheduler.add_job(
        scheduled_post_market_summary,
        'cron',
        day_of_week='mon-fri',
        hour=16,
        minute=5,
        id="post_market_summary",
        replace_existing=True
    )
    
    if not scheduler.running:
        scheduler.start()

def shutdown_scheduler():
    """Safely shuts down the scheduler."""
    logger.info("Shutting down APScheduler...")
    if scheduler.running:
        scheduler.shutdown(wait=False)
