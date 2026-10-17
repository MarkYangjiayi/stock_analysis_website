import asyncio
import logging
from contextlib import AsyncExitStack
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz
from datetime import date, datetime, time, timedelta

from core.config import settings
from services.daily_reporter import generate_morning_briefing, generate_post_market_summary
from services.weekly_digest import generate_weekly_digest
from services.screener_sync import run_screener_pipeline
from services.quant.factor_engine import compute_factors_for_date
from scripts.backup_sqlite import create_backup
from core.trading_calendar import is_us_market_session, latest_completed_us_session
from services.pipeline_runs import latest_published_date
from services.rrg_prices import refresh_rrg_price_history
from services.market_breadth import (
    backfill_market_breadth_price_history,
    refresh_market_breadth,
)
from services.index_valuation import refresh_index_valuation, refresh_member_split_histories
from services.universe import refresh_historical_universe_memberships
from services.rsi_monitor import run_daily_rsi_monitor
from services.themes.alerts import run_theme_alerts
from services.themes.bootstrap import bootstrap_themes
from services.themes.briefs import generate_theme_briefs
from services.themes.etf_prices import THEME_ETF_PRICE_DATASET, refresh_theme_etf_prices
from services.themes.membership import THEME_MEMBERSHIP_DATASET, refresh_theme_membership
from services.themes.news_ingest import NEWS_ATTENTION_DATASET, ingest_market_news
from services.themes.pipeline import THEME_TRENDS_DATASET, refresh_theme_trends, theme_trends_rerun_due
from services.themes.proposals import refresh_theme_proposals
from services.themes.taxonomy import sync_theme_taxonomy

logger = logging.getLogger(__name__)

# Force strictly New York timezone to avoid DST issues
ny_tz = pytz.timezone('America/New_York')

scheduler = AsyncIOScheduler(
    timezone=ny_tz,
    job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
)
_factor_sync_lock = asyncio.Lock()
_market_breadth_sync_lock = asyncio.Lock()
_index_valuation_sync_lock = asyncio.Lock()
_theme_etf_prices_sync_lock = asyncio.Lock()
_theme_news_sync_lock = asyncio.Lock()
_theme_trends_sync_lock = asyncio.Lock()
_theme_briefs_lock = asyncio.Lock()
_theme_alerts_lock = asyncio.Lock()
_theme_proposals_lock = asyncio.Lock()
_theme_membership_sync_lock = asyncio.Lock()
# The one-off theme cold start runs this long after the scheduler starts, so the
# blocking startup catch-up (screener/price_history) has already finished.
THEME_BOOTSTRAP_DELAY_SECONDS = 120
# (hour, minute) America/New_York of the Tue-Sat briefs/alerts slots.
THEME_BRIEFS_SLOT = (5, 5)
THEME_ALERTS_SLOT = (5, 15)
THEME_SLOT_WEEKDAYS = frozenset({1, 2, 3, 4, 5})  # date.weekday(): Tue-Sat


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
        try:
            await backfill_market_breadth_price_history(target)
        except Exception as exc:
            # Same policy as startup catch-up: the backfill gate is advisory, and
            # refresh_market_breadth applies its own per-date eligibility gate.
            logger.warning("Market breadth price backfill incomplete for %s: %s", target, exc)
        return await refresh_market_breadth(target)


async def scheduled_market_breadth_sync(reference_date: date = None):
    target = latest_completed_us_session(reference_date or datetime.now(ny_tz).date())
    return await _sync_market_breadth_for_target(target)


async def _sync_index_valuation_for_target(target: date):
    """Recompute the month-end series after membership and prices are current.

    The refresh itself is idempotent and dependency-gated; the lock only keeps
    concurrent triggers from racing on the pipeline run.
    """
    async with _index_valuation_sync_lock:
        published = await latest_published_date("index_valuation")
        if published is not None and published >= target:
            return {
                "status": "skipped",
                "reason": "already-published",
                "as_of_date": target.isoformat(),
            }
        try:
            await refresh_member_split_histories(target)
        except Exception as exc:
            logger.exception("Index member split refresh failed for %s: %s", target, exc)
        return await refresh_index_valuation(target)


async def scheduled_index_valuation_sync(reference_date: date = None):
    target = latest_completed_us_session(reference_date or datetime.now(ny_tz).date())
    return await _sync_index_valuation_for_target(target)


async def scheduled_morning_briefing():
    if is_us_market_session(datetime.now(ny_tz).date()):
        return await generate_morning_briefing()
    return None


async def scheduled_post_market_summary():
    if is_us_market_session(datetime.now(ny_tz).date()):
        return await generate_post_market_summary()
    return None


async def scheduled_weekly_digest():
    return await generate_weekly_digest()


async def scheduled_rsi_monitor(reference_date: date = None):
    return await run_daily_rsi_monitor(reference_date)


def _skipped(target: date) -> dict:
    return {"status": "skipped", "reason": "already-published", "as_of_date": target.isoformat()}


async def _theme_trends_published_for(target: date) -> bool:
    published = await latest_published_date(THEME_TRENDS_DATASET)
    return published is not None and published >= target


async def _sync_theme_etf_prices_for_target(target: date):
    async with _theme_etf_prices_sync_lock:
        published = await latest_published_date(THEME_ETF_PRICE_DATASET)
        if published is not None and published >= target:
            return _skipped(target)
        return await refresh_theme_etf_prices(target)


async def scheduled_theme_etf_prices_sync(reference_date: date = None):
    target = latest_completed_us_session(reference_date or datetime.now(ny_tz).date())
    return await _sync_theme_etf_prices_for_target(target)


async def _sync_theme_news_for_target(target: date):
    async with _theme_news_sync_lock:
        published = await latest_published_date(NEWS_ATTENTION_DATASET)
        if published is not None and published >= target:
            return _skipped(target)
        return await ingest_market_news(target)


async def scheduled_theme_news_sync(reference_date: date = None):
    target = latest_completed_us_session(reference_date or datetime.now(ny_tz).date())
    return await _sync_theme_news_for_target(target)


async def _sync_theme_trends_for_target(target: date):
    """Publish theme trends once per session.

    Not chained from the screener: that fires before the 04:40 news ingestion and
    would publish degraded attention. A publication made before news_attention
    existed gets one re-run (the 07:50 slot) once the news window is complete.
    """
    async with _theme_trends_sync_lock:
        published = await latest_published_date(THEME_TRENDS_DATASET)
        if published is not None and published >= target:
            if published > target or not await theme_trends_rerun_due(target):
                return _skipped(target)
        return await refresh_theme_trends(target)


def _theme_slot_passed(target: date, slot: tuple[int, int], now: datetime) -> bool:
    """Whether the Tue-Sat slot that serves ``target`` (the first slot day after it) has fired."""
    day = target + timedelta(days=1)
    while day.weekday() not in THEME_SLOT_WEEKDAYS:
        day += timedelta(days=1)
    return now >= ny_tz.localize(datetime.combine(day, time(*slot)))


async def _run_theme_followups(target: date, reference_date: date = None) -> dict:
    """Briefs and alerts whose own slot already passed for ``target``.

    The 05:05/05:15 jobs defer while theme_trends is unpublished and nothing else
    retries them, so a publication that lands later (the 07:50 slot, or a bootstrap
    that held the trends lock past them) would otherwise lose that session's briefs
    and lifecycle alerts. Re-running both is safe: briefs are cached by evidence
    hash and alerts dedupe on (theme, type, date). Each runs under its own lock;
    a failure is reported, never raised.
    """
    now = datetime.now(ny_tz)
    followups = {}
    for name, enabled, slot, job in (
        ("theme_briefs", settings.THEME_BRIEFS_ENABLED, THEME_BRIEFS_SLOT, scheduled_theme_briefs),
        ("theme_alerts", settings.THEME_ALERTS_ENABLED, THEME_ALERTS_SLOT, scheduled_theme_alerts),
    ):
        if not enabled or not _theme_slot_passed(target, slot, now):
            continue
        try:
            followups[name] = await job(reference_date)
        except Exception as exc:
            # Provider-backed (DeepSeek / notification) errors: log the type only.
            logger.error("Theme %s catch-up failed for %s: %s", name, target, type(exc).__name__)
            followups[name] = {"status": "failed", "reason": type(exc).__name__, "as_of_date": target.isoformat()}
    return followups


async def scheduled_theme_trends_sync(reference_date: date = None):
    target = latest_completed_us_session(reference_date or datetime.now(ny_tz).date())
    result = await _sync_theme_trends_for_target(target)
    # Published now or already: catch up briefs/alerts whose slot found nothing.
    if isinstance(result, dict) and result.get("status") in {"published", "skipped"}:
        followups = await _run_theme_followups(target, reference_date)
        if followups:
            result = {**result, **followups}
    return result


async def scheduled_theme_briefs(reference_date: date = None):
    target = latest_completed_us_session(reference_date or datetime.now(ny_tz).date())
    async with _theme_briefs_lock:
        if not await _theme_trends_published_for(target):
            return {"status": "deferred", "reason": "theme-trends-not-published", "as_of_date": target.isoformat()}
        return await generate_theme_briefs(target)


async def scheduled_theme_alerts(reference_date: date = None):
    target = latest_completed_us_session(reference_date or datetime.now(ny_tz).date())
    async with _theme_alerts_lock:
        if not await _theme_trends_published_for(target):
            return {"status": "deferred", "reason": "theme-trends-not-published", "as_of_date": target.isoformat()}
        # A sent alert cannot be withdrawn: when the 07:50 slot will replace this
        # publication (attention re-run), alert from the replacement instead; the
        # trends job runs the alert follow-up after it publishes.
        if await theme_trends_rerun_due(target):
            return {"status": "deferred", "reason": "theme-trends-rerun-pending", "as_of_date": target.isoformat()}
        # Alerts resolve their own publication and dedupe already-delivered events.
        return await run_theme_alerts(reference_date)


async def scheduled_theme_proposals(reference_date: date = None):
    target = latest_completed_us_session(reference_date or datetime.now(ny_tz).date())
    async with _theme_proposals_lock:
        if not await _theme_trends_published_for(target):
            return {"status": "deferred", "reason": "theme-trends-not-published", "as_of_date": target.isoformat()}
        return await refresh_theme_proposals(target)


async def scheduled_theme_membership_refresh(reference_date: date = None):
    as_of = latest_completed_us_session(reference_date or datetime.now(ny_tz).date())
    async with _theme_membership_sync_lock:
        published = await latest_published_date(THEME_MEMBERSHIP_DATASET)
        if published is not None and published >= as_of:
            return _skipped(as_of)
        await sync_theme_taxonomy()
        return await refresh_theme_membership(as_of)


async def scheduled_theme_bootstrap(reference_date: date = None):
    """Theme cold start; holds the publishing jobs' locks so a cron slot that fires
    meanwhile waits and then skips on its own guard instead of racing.

    ``bootstrap_themes`` never alerts; once it released the locks, this runs the
    briefs/alerts follow-ups whose slot already passed (a bootstrap after 07:50 has
    no trends slot left to catch them up)."""
    reference = reference_date or datetime.now(ny_tz).date()
    async with AsyncExitStack() as stack:
        for lock in (
            _theme_membership_sync_lock,
            _theme_etf_prices_sync_lock,
            _theme_news_sync_lock,
            _theme_trends_sync_lock,
        ):
            await stack.enter_async_context(lock)
        result = await bootstrap_themes(reference)
    if isinstance(result, dict) and result.get("theme_trends") in {"published", "current", "skipped"}:
        followups = await _run_theme_followups(latest_completed_us_session(reference), reference_date)
        if followups:
            result = {**result, **followups}
    return result


def _register_theme_jobs():
    """Trending-theme jobs (America/New_York); Tue-Sat maps to the preceding Mon-Fri session."""
    scheduler.add_job(
        scheduled_theme_etf_prices_sync,
        'cron',
        day_of_week='tue-sat',
        hour=4,
        minute=35,
        id="daily_theme_etf_prices",
        replace_existing=True,
    )
    scheduler.add_job(
        scheduled_theme_news_sync,
        'cron',
        day_of_week='tue-sat',
        hour=4,
        minute=40,
        id="daily_theme_news_sync",
        replace_existing=True,
    )
    # 04:50 primary; 07:50 covers a late screener or a news window that completed late
    # (the skip guard makes it free otherwise) and catches up briefs/alerts whose
    # 05:05/05:15 slot found no publication (see _run_theme_followups).
    scheduler.add_job(
        scheduled_theme_trends_sync,
        'cron',
        day_of_week='tue-sat',
        hour='4,7',
        minute=50,
        id="daily_theme_trends_sync",
        replace_existing=True,
    )
    if settings.THEME_BRIEFS_ENABLED:
        scheduler.add_job(
            scheduled_theme_briefs,
            'cron',
            day_of_week='tue-sat',
            hour=THEME_BRIEFS_SLOT[0],
            minute=THEME_BRIEFS_SLOT[1],
            id="daily_theme_briefs",
            replace_existing=True,
        )
    if settings.THEME_ALERTS_ENABLED:
        scheduler.add_job(
            scheduled_theme_alerts,
            'cron',
            day_of_week='tue-sat',
            hour=THEME_ALERTS_SLOT[0],
            minute=THEME_ALERTS_SLOT[1],
            id="daily_theme_alerts",
            replace_existing=True,
        )
    if settings.THEME_PROPOSALS_ENABLED:
        # After the 07:50 trends retry, so a late or failed 04:50 publication does not
        # cost the week's proposals (they defer while theme_trends is unpublished).
        scheduler.add_job(
            scheduled_theme_proposals,
            'cron',
            day_of_week='sat',
            hour=8,
            minute=30,
            id="weekly_theme_proposals",
            replace_existing=True,
        )
    # Sunday 05:00 stays clear of the 03:00 Sunday backup.
    scheduler.add_job(
        scheduled_theme_membership_refresh,
        'cron',
        day_of_week='sun',
        hour=5,
        minute=0,
        id="weekly_theme_membership_refresh",
        replace_existing=True,
    )
    scheduler.add_job(
        scheduled_theme_bootstrap,
        'date',
        run_date=datetime.now(ny_tz) + timedelta(seconds=THEME_BOOTSTRAP_DELAY_SECONDS),
        id="theme_bootstrap",
        replace_existing=True,
    )

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
    # Index-level valuation recomputes the full month-end P/E series from
    # stored statements and prices; it runs after breadth has confirmed the
    # day's membership and price publications.
    scheduler.add_job(
        scheduled_index_valuation_sync,
        'cron',
        day_of_week='tue-sat',
        hour=4,
        minute=15,
        id="daily_index_valuation_sync",
        replace_existing=True,
    )
    # Run after the daily price/factor pipeline. Tue-Sat maps to the preceding
    # Mon-Fri US market sessions.
    if settings.RSI_MONITOR_ENABLED:
        scheduler.add_job(
            scheduled_rsi_monitor,
            'cron',
            day_of_week='tue-sat',
            hour=4,
            minute=30,
            id="daily_rsi_monitor",
            replace_existing=True,
        )
    if settings.THEMES_ENABLED:
        _register_theme_jobs()
    scheduler.add_job(
        create_backup,
        'cron',
        day_of_week='sun',
        hour=3,
        minute=0,
        kwargs={'retention': 2},
        id="weekly_sqlite_backup",
        replace_existing=True,
    )
    # The delayed feed needs 15-20 minutes before the open is observable.
    scheduler.add_job(
        scheduled_morning_briefing,
        'cron',
        day_of_week='mon-fri',
        hour=settings.DAILY_REPORT_MORNING_HOUR,
        minute=settings.DAILY_REPORT_MORNING_MINUTE,
        id="morning_briefing",
        replace_existing=True
    )
    
    # Allow delayed quotes to reach the regular-session close.
    scheduler.add_job(
        scheduled_post_market_summary,
        'cron',
        day_of_week='mon-fri',
        hour=settings.DAILY_REPORT_CLOSE_HOUR,
        minute=settings.DAILY_REPORT_CLOSE_MINUTE,
        id="post_market_summary",
        replace_existing=True
    )
    
    if settings.WEEKLY_DIGEST_ENABLED:
        scheduler.add_job(
            scheduled_weekly_digest,
            'cron',
            day_of_week=settings.WEEKLY_DIGEST_DAY_OF_WEEK,
            hour=settings.WEEKLY_DIGEST_HOUR,
            minute=settings.WEEKLY_DIGEST_MINUTE,
            id="weekly_digest",
            replace_existing=True,
        )

    if not scheduler.running:
        scheduler.start()

def shutdown_scheduler():
    """Safely shuts down the scheduler."""
    logger.info("Shutting down APScheduler...")
    if scheduler.running:
        scheduler.shutdown(wait=False)
