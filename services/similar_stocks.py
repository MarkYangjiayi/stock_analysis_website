"""Local-only, same-window daily-return neighbours; never predictive scores."""
from collections import defaultdict
from datetime import timedelta
import re

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.time_utils import utc_now
from core.trading_calendar import is_us_market_session, us_market_close_utc
from models import DailyPrice, SecurityMaster, Ticker
from services.security_master import canonicalize_ticker

WINDOW = 60
MIN_CORRELATION = 0.65


def company_key(name, ticker):
    # Avoid recommending another share class of the same named issuer.
    value = re.sub(r"\bclass\s+[a-z0-9]+\b", "", (name or ticker).lower())
    return re.sub(r"[^a-z0-9]", "", value)


def rank_matches(prices, profiles, ticker, dates):
    def series(symbol):
        values = np.array([prices[symbol].get(day, np.nan) for day in dates], dtype=float)
        if not np.isfinite(values).all() or (values <= 0).any():
            return None
        returns = values[1:] / values[:-1] - 1
        if np.std(returns) < 1e-10:
            return None
        return values, returns

    target = series(ticker)
    if target is None:
        return None, [], 0
    base, daily = target
    matches = []
    eligible = 0
    for symbol, profile in profiles.items():
        if symbol == ticker or company_key(profile['name'], symbol) == company_key(profiles[ticker]['name'], ticker):
            continue
        candidate = series(symbol)
        if candidate is None:
            continue
        eligible += 1
        values, returns = candidate
        correlation = float(np.corrcoef(daily, returns)[0, 1])
        if not np.isfinite(correlation) or correlation < MIN_CORRELATION:
            continue
        matches.append({**profile, 'ticker': symbol, 'correlation': round(correlation, 6),
                        'returns': (values / values[0] - 1).tolist()})
    matches.sort(key=lambda item: (-item['correlation'], item['ticker']))
    unique, seen = [], set()
    for item in matches:
        key = company_key(item['name'], item['ticker'])
        if key not in seen:
            unique.append(item)
            seen.add(key)
        if len(unique) == 5:
            break
    return (base / base[0] - 1).tolist(), unique, eligible


async def get_similar_stocks(ticker: str, db: AsyncSession):
    ticker = canonicalize_ticker(ticker)
    response = dict(ticker=ticker, window_days=WINDOW, as_of=None, dates=[],
                    target_returns=[], matches=[], eligible_count=0, status='insufficient_history')
    if not ticker.endswith('.US'):
        return {**response, 'status': 'unsupported_market'}
    now = utc_now()
    end = now.date()
    if not is_us_market_session(end) or us_market_close_utc(end) > now:
        end -= timedelta(days=1)
    dates = []
    while len(dates) < WINDOW + 1:
        if is_us_market_session(end):
            dates.append(end)
        end -= timedelta(days=1)
    dates.reverse()
    response['as_of'] = dates[-1].isoformat()
    profiles = {row.ticker: dict(name=row.name, industry=row.industry or row.sector)
                for row in (await db.execute(
                    select(Ticker.ticker, Ticker.name, Ticker.industry, Ticker.sector)
                    .join(SecurityMaster, SecurityMaster.canonical_ticker == Ticker.ticker)
                    .where(SecurityMaster.is_active.is_(True),
                           SecurityMaster.asset_type == 'Common Stock',
                           Ticker.ticker.endswith('.US')))).all()}
    if ticker not in profiles:
        return {**response, 'status': 'unsupported_security'}
    rows = (await db.execute(select(DailyPrice.ticker, DailyPrice.date, DailyPrice.adjusted_close)
                            .join(SecurityMaster, SecurityMaster.canonical_ticker == DailyPrice.ticker)
                            .where(DailyPrice.date >= dates[0], DailyPrice.date <= dates[-1],
                                   SecurityMaster.is_active.is_(True),
                                   SecurityMaster.asset_type == 'Common Stock',
                                   DailyPrice.ticker.endswith('.US')))).all()
    prices = defaultdict(dict)
    for symbol, day, close in rows:
        if close is not None:
            prices[symbol][day] = float(close)
    target, matches, eligible = rank_matches(prices, profiles, ticker, dates)
    if target is None:
        return response
    return {**response, 'status': 'ok', 'dates': [day.isoformat() for day in dates],
            'target_returns': target, 'matches': matches, 'eligible_count': eligible}
