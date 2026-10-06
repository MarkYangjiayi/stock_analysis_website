from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from core.config import settings
from core.trading_calendar import us_market_status
from models import DailyPrice, Ticker
from services import eodhd_client, watchlist_quotes


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fresh_caches():
    watchlist_quotes.reset_caches()
    yield
    watchlist_quotes.reset_caches()


@pytest.mark.parametrize(
    ("now", "phase"),
    [
        (utc(2026, 10, 6, 15, 0), "open"),    # Tue 11:00 ET
        (utc(2026, 10, 6, 12, 0), "pre"),     # Tue 08:00 ET
        (utc(2026, 10, 6, 21, 0), "post"),    # Tue 17:00 ET
        (utc(2026, 10, 7, 2, 0), "closed"),   # Tue 22:00 ET
        (utc(2026, 10, 10, 15, 0), "closed"), # Saturday
        (utc(2026, 11, 26, 15, 0), "closed"), # Thanksgiving
        (utc(2026, 11, 27, 18, 30), "post"),  # Early close at 13:00 ET
    ],
)
def test_market_status_uses_exchange_calendar(now, phase):
    assert us_market_status(now)["phase"] == phase


def test_market_status_reports_next_open_across_holiday():
    status = us_market_status(utc(2026, 11, 26, 15, 0))
    assert status["session_date"] is None
    assert status["next_open"] == utc(2026, 11, 27, 14, 30)

    early_close = us_market_status(utc(2026, 11, 27, 15, 0))
    assert early_close["closes_at"] == utc(2026, 11, 27, 18, 0)


def test_quote_ttl_refreshes_only_while_quotes_can_move():
    open_now = utc(2026, 10, 6, 15, 0)
    assert watchlist_quotes.quote_ttl(us_market_status(open_now), open_now) == watchlist_quotes.LIVE_TTL
    settling = utc(2026, 10, 6, 20, 10)
    assert watchlist_quotes.quote_ttl(us_market_status(settling), settling) == watchlist_quotes.LIVE_TTL
    night = utc(2026, 10, 7, 2, 0)
    assert watchlist_quotes.quote_ttl(us_market_status(night), night) == watchlist_quotes.IDLE_TTL


def test_normalize_quote_rejects_mismatched_or_missing_prices():
    raw = {"code": "AAPL.US", "close": 101.0, "previousClose": 100.0, "timestamp": 1_791_302_400}
    quote = watchlist_quotes.normalize_quote("AAPL.US", raw)
    assert quote["change"] == pytest.approx(1.0)
    assert quote["change_pct"] == pytest.approx(0.01)
    assert watchlist_quotes.normalize_quote("MSFT.US", raw) is None
    assert watchlist_quotes.normalize_quote("AAPL.US", {**raw, "close": "NA"}) is None
    assert watchlist_quotes.normalize_quote("AAPL.US", {**raw, "previousClose": "NA"})["change_pct"] is None


async def _seed_history(db_session, ticker: str, last: date, sessions: int) -> None:
    db_session.add(Ticker(ticker=ticker, name=ticker))
    for offset in range(sessions):
        db_session.add(DailyPrice(
            ticker=ticker,
            date=last - timedelta(days=sessions - 1 - offset),
            close=Decimal(100 + offset),
            adjusted_close=Decimal(100 + offset),
        ))
    await db_session.commit()


@pytest.mark.asyncio
async def test_quotes_use_local_history_fallback_eod_and_cache(db_session, monkeypatch):
    now = utc(2026, 10, 6, 15, 0)
    await _seed_history(db_session, "AAPL.US", date(2026, 10, 5), 40)
    live_calls: list[list[str]] = []
    eod_calls: list[str] = []

    async def fake_live(tickers, client=None):
        live_calls.append(list(tickers))
        return [
            {"code": "AAPL.US", "close": 150.0, "previousClose": 139.0, "timestamp": now.timestamp() - 900},
            {"code": "MSFT.US", "close": 400.0, "previousClose": 410.0, "timestamp": now.timestamp() - 900},
        ]

    async def fake_eod(ticker, from_date=None, to_date=None, client=None):
        eod_calls.append(ticker)
        return [
            {"date": "2026-10-02", "close": 405.0, "adjusted_close": 405.0},
            {"date": "2026-10-05", "close": 410.0, "adjusted_close": 410.0},
        ]

    monkeypatch.setattr(eodhd_client, "get_live_quotes", fake_live)
    monkeypatch.setattr(eodhd_client, "get_eod_historical_data", fake_eod)

    result = await watchlist_quotes.get_watchlist_quotes(db_session, ["AAPL.US", "MSFT.US"], now=now)
    aapl, msft = result["items"]

    assert result["market"]["phase"] == "open"
    assert aapl["quote"]["change_pct"] == pytest.approx(11 / 139)
    assert len(aapl["sparkline"]) == watchlist_quotes.SPARKLINE_SESSIONS
    assert aapl["sparkline"][-1] == {"date": date(2026, 10, 6), "close": 150.0}
    assert aapl["sparkline"][-2]["date"] == date(2026, 10, 5)
    assert [point["close"] for point in msft["sparkline"]] == [405.0, 410.0, 400.0]
    assert eod_calls == ["MSFT.US"]

    await watchlist_quotes.get_watchlist_quotes(db_session, ["AAPL.US", "MSFT.US"], now=now + timedelta(seconds=30))
    assert len(live_calls) == 1
    assert eod_calls == ["MSFT.US"]

    await watchlist_quotes.get_watchlist_quotes(db_session, ["AAPL.US", "MSFT.US"], now=now + timedelta(seconds=61))
    assert len(live_calls) == 2


@pytest.mark.asyncio
async def test_provider_failure_keeps_last_quote(db_session, monkeypatch):
    now = utc(2026, 10, 6, 15, 0)
    await _seed_history(db_session, "AAPL.US", date(2026, 10, 5), 30)

    async def first(tickers, client=None):
        return [{"code": "AAPL.US", "close": 150.0, "previousClose": 149.0, "timestamp": now.timestamp()}]

    async def failing(tickers, client=None):
        raise TimeoutError

    monkeypatch.setattr(eodhd_client, "get_live_quotes", first)
    await watchlist_quotes.get_watchlist_quotes(db_session, ["AAPL.US"], now=now)
    monkeypatch.setattr(eodhd_client, "get_live_quotes", failing)
    later = await watchlist_quotes.get_watchlist_quotes(db_session, ["AAPL.US"], now=now + timedelta(minutes=5))
    assert later["items"][0]["quote"]["price"] == 150.0


@pytest.mark.asyncio
async def test_exhausted_provider_budget_degrades_without_calls(db_session, monkeypatch):
    now = utc(2026, 10, 6, 15, 0)
    calls: list[list[str]] = []

    async def fake_live(tickers, client=None):
        calls.append(list(tickers))
        return []

    async def fake_eod(*args, **kwargs):
        calls.append(["eod"])
        return []

    monkeypatch.setattr(eodhd_client, "get_live_quotes", fake_live)
    monkeypatch.setattr(eodhd_client, "get_eod_historical_data", fake_eod)
    monkeypatch.setattr(settings, "WATCHLIST_PROVIDER_SYMBOLS_PER_MINUTE", 0)

    result = await watchlist_quotes.get_watchlist_quotes(db_session, ["NVDA.US"], now=now)
    assert calls == []
    assert result["items"] == [{"ticker": "NVDA.US", "quote": None, "sparkline": []}]


@pytest.mark.asyncio
async def test_watchlist_quote_endpoint_validates_and_canonicalizes(monkeypatch):
    from main import app
    from api import routers

    seen: list[list[str]] = []

    async def fake_quotes(db, tickers):
        seen.append(tickers)
        return {"market": us_market_status(utc(2026, 10, 6, 15, 0)), "delay_minutes": 15, "items": []}

    monkeypatch.setattr(routers, "get_watchlist_quotes", fake_quotes)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.get("/api/v1/watchlist/quotes", params={"tickers": "aapl, MSFT.US,AAPL.US"})
        too_many = await client.get("/api/v1/watchlist/quotes", params={"tickers": ",".join(f"T{i}" for i in range(21))})
        invalid = await client.get("/api/v1/watchlist/quotes", params={"tickers": "AAPL;DROP"})
        empty = await client.get("/api/v1/watchlist/quotes", params={"tickers": " , "})
        status_response = await client.get("/api/v1/market/status")

    assert ok.status_code == 200
    assert seen == [["AAPL.US", "MSFT.US"]]
    assert too_many.status_code == 422
    assert invalid.status_code == 422
    assert empty.status_code == 422
    assert status_response.status_code == 200
    assert status_response.json()["phase"] in {"pre", "open", "post", "closed"}
