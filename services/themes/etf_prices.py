"""Daily proxy-ETF price history for theme proxy lines and theme RRG."""

from __future__ import annotations

import logging
from datetime import date
from typing import Mapping, Optional

from core.trading_calendar import is_us_market_session, latest_completed_us_session
from database import async_session_maker
from services.history_backfill import backfill_price_history
from services.market_breadth import market_sessions_through
from services.themes.membership import load_proxy_inception
from services.themes.taxonomy import load_taxonomy

logger = logging.getLogger(__name__)

THEME_ETF_PRICE_DATASET = "theme_etf_prices"
# 352 + the target session = the 353-session window theme RRG (252 + 100) needs.
THEME_ETF_HISTORY_DAYS = 352
THEME_ETF_BENCHMARK = "SPY.US"
THEME_ETF_MINIMUM_COVERAGE = 0.80


def required_sessions_by_etf(
    tickers: list[str],
    sessions: list[date],
    inception: Mapping[str, Optional[date]],
) -> tuple[dict[str, list[date]], list[str]]:
    """Sessions each ETF must have priced: only those after its known inception.

    A fund whose inception is after the window's last session is not listed yet and is
    returned separately; unknown inception (None) requires the whole window.
    """
    required: dict[str, list[date]] = {}
    not_listed: list[str] = []
    target = sessions[-1]
    for ticker in tickers:
        listed = inception.get(ticker)
        if listed is None:
            required[ticker] = list(sessions)
        elif listed > target:
            not_listed.append(ticker)
        else:
            # The provider's first bar can lag the listing date, so the inception day is optional.
            required[ticker] = [session for session in sessions if session > listed] or [target]
    return required, not_listed


async def refresh_theme_etf_prices(target: date) -> dict:
    """Backfill every non-retired proxy ETF plus SPY through ``target`` and publish ``theme_etf_prices``.

    ETFs are written to ``tickers``/``daily_prices`` only (never ``security_master``), so they
    cannot leak into the screener's common-stock universe.
    """
    session = target if is_us_market_session(target) else latest_completed_us_session(target)
    tickers = list(dict.fromkeys([*load_taxonomy().proxy_etfs(), THEME_ETF_BENCHMARK]))
    async with async_session_maker() as db:
        inception = await load_proxy_inception(db)
    sessions = market_sessions_through(session, THEME_ETF_HISTORY_DAYS + 1)
    required, not_listed = required_sessions_by_etf(tickers, sessions, inception)
    if not_listed:
        logger.info("Theme ETFs not listed by %s are skipped: %s", session, ", ".join(not_listed))
    result = await backfill_price_history(
        list(required),
        history_days=THEME_ETF_HISTORY_DAYS,
        target_date=session,
        include_corporate_actions=False,
        include_target_session=True,
        publication_dataset=THEME_ETF_PRICE_DATASET,
        publish_when_complete=True,
        minimum_ticker_coverage=THEME_ETF_MINIMUM_COVERAGE,
        required_sessions_by_ticker=required,
    )
    return {**result, "as_of_date": result.get("as_of_date") or session.isoformat(), "not_listed": not_listed}
