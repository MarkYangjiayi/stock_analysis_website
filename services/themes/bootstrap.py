"""Non-blocking cold start / catch-up for theme jobs (runs as a one-off scheduler job).

``worker.py`` keeps its blocking startup catch-up unchanged; this runs ~120 s after
the scheduler starts so a slow theme cold start (membership verification, news
backfill) never delays the core publications. Each step is guarded the same way as
its daily job, failures are logged and the next step still runs, and nothing here
sends notifications.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from core.config import settings
from core.trading_calendar import latest_completed_us_session
from services.pipeline_runs import latest_published_date
from services.themes.etf_prices import THEME_ETF_PRICE_DATASET, refresh_theme_etf_prices
from services.themes.membership import THEME_MEMBERSHIP_DATASET, refresh_theme_membership
from services.themes.news_ingest import NEWS_ATTENTION_DATASET, ingest_market_news
from services.themes.pipeline import THEME_TRENDS_DATASET, refresh_theme_trends, theme_trends_rerun_due
from services.themes.taxonomy import sync_theme_taxonomy

logger = logging.getLogger(__name__)

NEW_YORK = ZoneInfo("America/New_York")


def _status(result: Any) -> str:
    return str(result.get("status", "unknown")) if isinstance(result, dict) else "unknown"


async def bootstrap_themes(
    reference_date: Optional[date] = None,
    *,
    max_news_backfill_days: Optional[int] = None,
) -> dict:
    """Bring theme publications current for the latest completed session.

    Order: taxonomy sync -> membership (only when none is published or the latest is
    older than ``THEME_MEMBERSHIP_MAX_AGE_DAYS``) -> proxy-ETF prices -> news (one
    bounded batch: ``max_news_backfill_days`` or ``THEME_NEWS_BACKFILL_DAYS_PER_RUN``)
    -> trends for the target unless already published. ``reference_date`` is the New
    York calendar date (default: now in New York).
    """
    reference = reference_date or datetime.now(NEW_YORK).date()
    target = latest_completed_us_session(reference)
    result: dict[str, Any] = {
        "target_date": target.isoformat(),
        "taxonomy": "deferred",
        "theme_membership": "deferred",
        "theme_etf_prices": "deferred",
        "news_attention": "deferred",
        "theme_trends": "deferred",
    }
    if not settings.THEMES_ENABLED:
        return {**result, "status": "disabled"}

    try:
        taxonomy = await sync_theme_taxonomy()
        result["taxonomy"] = "synced"
        result["taxonomy_version"] = taxonomy.get("version") if isinstance(taxonomy, dict) else None
    except Exception as exc:
        result["taxonomy"] = "failed"
        logger.exception("Theme bootstrap: taxonomy sync failed: %s", exc)

    try:
        membership_published = await latest_published_date(THEME_MEMBERSHIP_DATASET)
        max_age = int(settings.THEME_MEMBERSHIP_MAX_AGE_DAYS)
        if membership_published is not None and (target - membership_published).days <= max_age:
            result["theme_membership"] = "current"
        else:
            result["theme_membership"] = _status(await refresh_theme_membership(target))
    except Exception as exc:
        result["theme_membership"] = "failed"
        # Provider-backed steps log the exception type only (provider errors can carry the token).
        logger.error("Theme bootstrap: membership refresh failed for %s: %s", target, type(exc).__name__)

    try:
        etf_published = await latest_published_date(THEME_ETF_PRICE_DATASET)
        if etf_published is not None and etf_published >= target:
            result["theme_etf_prices"] = "current"
        else:
            result["theme_etf_prices"] = _status(await refresh_theme_etf_prices(target))
    except Exception as exc:
        result["theme_etf_prices"] = "failed"
        logger.error("Theme bootstrap: proxy ETF prices failed for %s: %s", target, type(exc).__name__)

    try:
        news_published = await latest_published_date(NEWS_ATTENTION_DATASET)
        if news_published is not None and news_published >= target:
            result["news_attention"] = "current"
        else:
            result["news_attention"] = _status(
                await ingest_market_news(target, max_backfill_days=max_news_backfill_days)
            )
    except Exception as exc:
        # Includes EODHDQuotaExceeded: trends still publish with degraded attention.
        result["news_attention"] = "failed"
        logger.error("Theme bootstrap: news ingestion failed for %s: %s", target, type(exc).__name__)

    try:
        trends_published = await latest_published_date(THEME_TRENDS_DATASET)
        if (
            trends_published is not None
            and trends_published >= target
            and not (trends_published == target and await theme_trends_rerun_due(target))
        ):
            result["theme_trends"] = "current"
        else:
            result["theme_trends"] = _status(await refresh_theme_trends(target))
    except Exception as exc:
        result["theme_trends"] = "failed"
        logger.exception("Theme bootstrap: trends failed for %s: %s", target, exc)

    logger.info("Theme bootstrap finished: %s", result)
    return result
