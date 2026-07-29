import logging
from datetime import date
from typing import Optional

from core.trading_calendar import latest_completed_us_session
from services.pipeline_runs import latest_published_date
from services.quant.factor_engine import compute_latest_factors
from services.screener_sync import run_screener_pipeline
from services.history_backfill import backfill_latest_screener_dividends_once


logger = logging.getLogger(__name__)


async def catch_up_latest_publications(reference_date: Optional[date] = None) -> dict:
    """Recover the latest safe publication after downtime.

    We deliberately do not fabricate every missed historical snapshot with
    today's universe. Only the latest completed session is captured.
    """
    target = latest_completed_us_session(reference_date or date.today())
    latest_screener = await latest_published_date("screener")
    result = {
        "target_date": target.isoformat(),
        "screener": "current",
        "factors": "current",
        "dividend_history": "deferred",
    }
    if latest_screener is None or latest_screener < target:
        await run_screener_pipeline(target.isoformat(), observe_current_universe=True)
        result["screener"] = "published"
        result["dividend_history"] = "published"
    else:
        dividend_result = await backfill_latest_screener_dividends_once(target)
        result["dividend_history"] = dividend_result["status"]
    latest_factors = await latest_published_date("factors")
    if latest_factors is None or latest_factors < target:
        await compute_latest_factors()
        result["factors"] = "published"
    return result
