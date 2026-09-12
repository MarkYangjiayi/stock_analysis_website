from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from main import app
from models import Ticker, SecurityMaster, DailyPrice
from services.similar_stocks import rank_matches, get_similar_stocks
from core.trading_calendar import is_us_market_session


def test_ranking_alignment_missing_data_flat_inverse_and_duplicate_issuers():
    dates = list(range(61))
    rng = np.random.default_rng(42)
    daily = rng.normal(0.001, .02, 60)
    prices = defaultdict(dict)
    profiles = {}
    for i, (ticker, name, returns) in enumerate([
        ('A.US', 'Alpha Class A', daily), ('B.US', 'Beta', daily * 2),
        ('C.US', 'Inverse', -daily), ('D.US', 'Flat', np.zeros(60)),
        ('E.US', 'Missing', daily), ('F.US', 'Alpha Class B', daily),
        ('G.US', 'Noise', rng.normal(0, .02, 60)),
    ]):
        values = np.r_[1, np.cumprod(1 + returns)] * (10 + i)
        prices[ticker] = dict(zip(dates, values))
        profiles[ticker] = dict(name=name, industry='Test')
    del prices['E.US'][30]
    base, matches, eligible = rank_matches(prices, profiles, 'A.US', dates)
    assert [m['ticker'] for m in matches] == ['B.US']
    assert eligible == 3
    assert matches[0]['correlation'] == pytest.approx(1)
    assert base[0] == matches[0]['returns'][0] == 0
    assert base[-1] != matches[0]['returns'][-1]
    del prices['A.US'][15]
    assert rank_matches(prices, profiles, 'A.US', dates)[0] is None


@pytest.mark.asyncio
async def test_endpoint_uses_adjusted_prices_completed_sessions_and_security_type(db_session, monkeypatch):
    monkeypatch.setattr('services.similar_stocks.utc_now', lambda: datetime(2026, 9, 12, 12))
    dates = []
    day = datetime(2026, 9, 11).date()
    while len(dates) < 61:
        if is_us_market_session(day):
            dates.append(day)
        day -= timedelta(days=1)
    dates.reverse()
    returns = np.random.default_rng(3).normal(.001, .015, 60)
    values = np.r_[100, 100 * np.cumprod(1 + returns)]
    for ticker, asset in [('A.US', 'Common Stock'), ('B.US', 'Common Stock'), ('ETF.US', 'ETF')]:
        db_session.add(Ticker(ticker=ticker, name=ticker, industry='Software'))
        db_session.add(SecurityMaster(canonical_ticker=ticker, asset_type=asset, is_active=True))
        await db_session.flush()
        db_session.add_all([DailyPrice(ticker=ticker, date=d, adjusted_close=float(v), close=100) for d, v in zip(dates, values)])
    await db_session.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/stocks/A/similar')
    assert response.status_code == 200
    data = response.json()
    assert data['ticker'] == 'A.US'
    assert data['as_of'] == '2026-09-11'
    assert len(data['dates']) == 61
    assert [m['ticker'] for m in data['matches']] == ['B.US']
    assert data['target_returns'][-1] == pytest.approx(values[-1] / 100 - 1)
    assert (await get_similar_stocks('A.HK', db_session))['status'] == 'unsupported_market'
    # A missing last session must not silently shift the recommendation backwards.
    from sqlalchemy import delete
    await db_session.execute(delete(DailyPrice).where(DailyPrice.ticker == 'A.US', DailyPrice.date == dates[-1]))
    assert (await get_similar_stocks('A.US', db_session))['status'] == 'insufficient_history'
