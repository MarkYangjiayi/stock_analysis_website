from datetime import date, datetime, time, timezone
from typing import Literal, Optional, TypedDict
from zoneinfo import ZoneInfo

import pandas as pd
from exchange_calendars.exchange_calendar_xnys import XNYSExchangeCalendar


# Unscheduled NYSE closures that the pinned exchange_calendars release (4.5.x) does not
# know about. Treating them as sessions makes every price-history gate demand a bar the
# provider can never return.
ADHOC_CLOSURES = (
    pd.Timestamp("2025-01-09"),  # National Day of Mourning for President Jimmy Carter
)


class _XNYSWithAdhocClosures(XNYSExchangeCalendar):
    @property
    def adhoc_holidays(self):
        return [*super().adhoc_holidays, *ADHOC_CLOSURES]


_XNYS = _XNYSWithAdhocClosures()


def is_us_market_session(session_date: date) -> bool:
    return bool(_XNYS.is_session(pd.Timestamp(session_date)))


def latest_completed_us_session(reference_date: date) -> date:
    label = _XNYS.date_to_session(pd.Timestamp(reference_date) - pd.Timedelta(days=1), direction="previous")
    return label.date()


def us_market_close_utc(session_date: date) -> datetime:
    """Return a naive UTC timestamp for the official XNYS session close."""
    close = _XNYS.session_close(pd.Timestamp(session_date))
    return close.tz_convert("UTC").to_pydatetime().replace(tzinfo=None)


NEW_YORK = ZoneInfo("America/New_York")
PRE_MARKET_START = time(4, 0)
AFTER_HOURS_END = time(20, 0)

MarketPhase = Literal["pre", "open", "post", "closed"]


class UsMarketStatus(TypedDict):
    phase: MarketPhase
    session_date: Optional[date]
    opens_at: Optional[datetime]
    closes_at: Optional[datetime]
    next_open: datetime
    as_of: datetime


def _session_bounds(session_date: date) -> tuple[datetime, datetime]:
    label = pd.Timestamp(session_date)
    return (
        _XNYS.session_open(label).to_pydatetime(),
        _XNYS.session_close(label).to_pydatetime(),
    )


def us_market_status(now: datetime) -> UsMarketStatus:
    """Classify an aware timestamp against the XNYS session, honouring holidays and early closes."""
    now_utc = now.astimezone(timezone.utc)
    today = now_utc.astimezone(NEW_YORK).date()
    phase: MarketPhase = "closed"
    opens_at = closes_at = None
    if is_us_market_session(today):
        opens_at, closes_at = _session_bounds(today)
        pre_start = datetime.combine(today, PRE_MARKET_START, NEW_YORK)
        post_end = datetime.combine(today, AFTER_HOURS_END, NEW_YORK)
        if opens_at <= now_utc < closes_at:
            phase = "open"
        elif pre_start <= now_utc < opens_at:
            phase = "pre"
        elif closes_at <= now_utc < post_end:
            phase = "post"

    if opens_at is not None and now_utc < opens_at:
        next_open = opens_at
    else:
        following = _XNYS.date_to_session(pd.Timestamp(today) + pd.Timedelta(days=1), direction="next")
        next_open = _XNYS.session_open(following).to_pydatetime()
    return {
        "phase": phase,
        "session_date": today if opens_at is not None else None,
        "opens_at": opens_at,
        "closes_at": closes_at,
        "next_open": next_open,
        "as_of": now_utc,
    }
