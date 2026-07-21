from datetime import date, datetime

import exchange_calendars as exchange_calendars
import pandas as pd


_XNYS = exchange_calendars.get_calendar("XNYS")


def is_us_market_session(session_date: date) -> bool:
    return bool(_XNYS.is_session(pd.Timestamp(session_date)))


def latest_completed_us_session(reference_date: date) -> date:
    label = _XNYS.date_to_session(pd.Timestamp(reference_date) - pd.Timedelta(days=1), direction="previous")
    return label.date()


def us_market_close_utc(session_date: date) -> datetime:
    """Return a naive UTC timestamp for the official XNYS session close."""
    close = _XNYS.session_close(pd.Timestamp(session_date))
    return close.tz_convert("UTC").to_pydatetime().replace(tzinfo=None)
