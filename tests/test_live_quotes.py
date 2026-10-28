import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from core.config import settings
from core.trading_calendar import us_market_status
from models import DailyPrice, LiveQuote, LiveQuoteFeed, LiveQuoteLease, Ticker
from services import eodhd_client, live_quotes, watchlist_quotes
from services.live_quotes import LiveQuoteHub


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def naive(moment: datetime) -> datetime:
    return moment.replace(tzinfo=None)


def ms(moment: datetime) -> float:
    return moment.timestamp() * 1000


OPEN = utc(2026, 10, 6, 15, 0)    # Tue 11:00 ET
CLOSE = utc(2026, 10, 6, 20, 0)   # Tue 16:00 ET
SESSION = date(2026, 10, 6)


def _eod(close, calls=None):
    async def fake(ticker, from_date=None, to_date=None, client=None):
        if calls is not None:
            calls.append(ticker)
        return [] if close is None else [{"date": from_date, "close": close, "adjusted_close": close}]
    return fake


@pytest.fixture(autouse=True)
def fresh_caches(monkeypatch):
    watchlist_quotes.reset_caches()
    live_quotes._official_closes.clear()
    # No EOD bar for the session unless a test publishes one.
    monkeypatch.setattr(eodhd_client, "get_eod_historical_data", _eod(None))
    yield
    watchlist_quotes.reset_caches()
    live_quotes._official_closes.clear()


class FakeSocket:
    def __init__(self):
        self.sent: list[dict] = []
        self.incoming: asyncio.Queue = asyncio.Queue()

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.incoming.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def test_ws_symbols_map_site_tickers():
    assert live_quotes.ws_symbol("MELI.US") == "MELI"
    assert live_quotes.ws_symbol("brk-b.us") == "BRK.B"
    assert live_quotes.ws_symbol("0700.HK") is None
    assert live_quotes.ws_symbol("GSPC.INDX") is None
    assert live_quotes.site_ticker("BRK.B") == "BRK-B.US"


def test_only_regular_session_trades_count():
    assert live_quotes.regular_session_date(utc(2026, 10, 6, 13, 30)) == SESSION
    assert live_quotes.regular_session_date(CLOSE - timedelta(milliseconds=100)) == SESSION
    # No closing auction print is streamed: from the bell on, prints are extended hours
    # (AAPL printed 347.15 four seconds after the 2026-10-27 bell; the close was 347.10).
    assert live_quotes.regular_session_date(CLOSE) is None
    assert live_quotes.regular_session_date(CLOSE + timedelta(seconds=4)) is None
    assert live_quotes.regular_session_date(utc(2026, 10, 6, 13, 29)) is None  # pre-market
    assert live_quotes.regular_session_date(CLOSE + timedelta(minutes=1)) is None  # after hours
    assert live_quotes.regular_session_date(utc(2026, 10, 10, 15, 0)) is None  # Saturday
    assert live_quotes.in_live_window(OPEN)
    assert live_quotes.in_live_window(CLOSE + timedelta(minutes=4))
    assert not live_quotes.in_live_window(CLOSE + timedelta(minutes=6))
    assert not live_quotes.in_live_window(utc(2026, 10, 6, 12, 0))


@pytest.mark.asyncio
async def test_desired_symbols_pin_monitor_then_newest_leases_within_budget(db_session, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WS_MONITOR", True)
    monkeypatch.setattr(settings, "WS_SYMBOLS", "nvda")
    for ticker, seconds in [("AAPL.US", 50), ("MELI.US", 55), ("BRK-B.US", -100), ("UBER.US", -130), ("0700.HK", 59)]:
        db_session.add(LiveQuoteLease(ticker=ticker, expires_at=naive(OPEN + timedelta(seconds=seconds))))
    await db_session.commit()
    hub = LiveQuoteHub()

    # A lease lapsed 100 s ago is still inside the two-minute grace; 130 s ago is not.
    assert await hub.desired_symbols(OPEN) == ["NVDA", "MELI", "AAPL", "BRK.B"]
    monkeypatch.setattr(settings, "LIVE_QUOTE_MAX_SYMBOLS", 2)
    assert await hub.desired_symbols(OPEN) == ["NVDA", "MELI"]
    assert await hub.desired_symbols(utc(2026, 10, 7, 2, 0)) == ["NVDA"]
    monkeypatch.setattr(settings, "ENABLE_WS_MONITOR", False)
    assert await hub.desired_symbols(utc(2026, 10, 7, 2, 0)) == []


@pytest.mark.asyncio
async def test_hub_keeps_newest_regular_trade_and_heartbeat(db_session, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WS_MONITOR", True)
    monkeypatch.setattr(settings, "WS_SYMBOLS", "MELI")
    ticks: list[tuple[str, float]] = []

    async def record(symbol, price):
        ticks.append((symbol, price))

    monkeypatch.setattr(live_quotes.ws_monitor, "process_tick", record)
    hub = LiveQuoteHub()
    socket = FakeSocket()
    await hub._reconcile(socket, ["MELI", "BRK.B"])
    hub._connected = True

    await hub.on_trade({"s": "MELI", "p": 1635.0, "t": ms(OPEN), "ms": "open"})
    await hub.on_trade({"s": "MELI", "p": 1630.0, "t": ms(OPEN - timedelta(seconds=5))})  # reported late
    await hub.on_trade({"s": "MELI", "p": 1700.0, "t": ms(CLOSE + timedelta(minutes=10))})  # after hours
    await hub.on_trade({"s": "BRK.B", "p": "NA", "t": ms(OPEN)})
    await hub.flush()

    row = await db_session.get(LiveQuote, "MELI.US")
    assert (row.price, row.traded_at, row.session_date) == (1635.0, naive(OPEN), SESSION)
    assert await db_session.get(LiveQuote, "BRK-B.US") is None
    feed = await db_session.get(LiveQuoteFeed, live_quotes.FEED_ID)
    assert feed.connected and feed.tickers == ["BRK-B.US", "MELI.US"]
    # The move monitor still sees every MELI print, extended hours included.
    assert ticks == [("MELI", 1635.0), ("MELI", 1630.0), ("MELI", 1700.0)]
    assert socket.sent == [{"action": "subscribe", "symbols": "MELI,BRK.B"}]


@pytest.mark.asyncio
async def test_reconcile_unsubscribes_and_rejected_symbols_wait(db_session):
    db_session.add(LiveQuoteLease(ticker="MELI.US", expires_at=naive(OPEN + timedelta(seconds=30))))
    await db_session.commit()
    hub = LiveQuoteHub()
    socket = FakeSocket()
    await hub._reconcile(socket, ["MELI", "AAPL"])
    await hub._reconcile(socket, ["MELI"])
    assert socket.sent == [
        {"action": "subscribe", "symbols": "MELI,AAPL"},
        {"action": "unsubscribe", "symbols": "AAPL"},
    ]

    await hub._reconcile(socket, ["MELI", "NVDA"])
    hub.on_status({"status_code": 422, "message": "Symbols limit reached"})
    assert hub._subscribed == {"MELI"}
    assert await hub.desired_symbols(OPEN) == ["MELI"]


@pytest.mark.asyncio
async def test_session_closes_when_nothing_is_wanted_outside_the_session(monkeypatch):
    socket = FakeSocket()
    hub = LiveQuoteHub(connect=lambda url: socket, clock=lambda: utc(2026, 10, 7, 2, 0))

    async def nothing(now):
        return []

    monkeypatch.setattr(hub, "desired_symbols", nothing)
    assert await asyncio.wait_for(hub._session(), timeout=2) is True
    assert not hub._connected


@pytest.mark.asyncio
async def test_hub_backs_off_after_short_failed_connections(monkeypatch):
    hub = LiveQuoteHub()
    sessions = 0
    sleeps: list[float] = []

    async def wanted(now):
        return ["MELI"]

    async def failing_session():
        nonlocal sessions
        sessions += 1
        if sessions == 3:
            raise asyncio.CancelledError
        raise ConnectionError("reset")

    async def no_flush():
        return None

    async def record_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(hub, "desired_symbols", wanted)
    monkeypatch.setattr(hub, "_session", failing_session)
    monkeypatch.setattr(hub, "flush", no_flush)
    monkeypatch.setattr("services.live_quotes.asyncio.sleep", record_sleep)

    with pytest.raises(asyncio.CancelledError):
        await hub.run()
    assert sleeps == [1, 2]


async def _seed(db_session, *, last_bar: date = date(2026, 10, 5)) -> None:
    db_session.add(Ticker(ticker="MELI.US", name="MercadoLibre"))
    db_session.add(DailyPrice(ticker="MELI.US", date=last_bar, close=Decimal("1810.15"), adjusted_close=Decimal("1810.15")))
    await db_session.commit()


def _delayed(price: float, as_of: datetime, previous_close: float = 1810.15):
    async def fake(tickers, client=None):
        return [{"code": "MELI.US", "close": price, "previousClose": previous_close, "timestamp": as_of.timestamp()}]
    return fake


async def _stream(db_session, *, heartbeat: datetime, traded_at: datetime, price: float = 1635.87) -> None:
    db_session.add(LiveQuoteFeed(id=live_quotes.FEED_ID, connected=True, tickers=["MELI.US"], heartbeat_at=naive(heartbeat)))
    db_session.add(LiveQuote(
        ticker="MELI.US", price=price, traded_at=naive(traded_at),
        session_date=traded_at.astimezone(live_quotes.NEW_YORK).date(), received_at=naive(traded_at),
    ))
    await db_session.commit()


@pytest.mark.asyncio
async def test_open_session_serves_streamed_trade_and_records_lease(db_session, monkeypatch):
    await _seed(db_session)
    await _stream(db_session, heartbeat=OPEN - timedelta(seconds=3), traded_at=OPEN - timedelta(seconds=1))
    monkeypatch.setattr(eodhd_client, "get_live_quotes", _delayed(1614.80, OPEN - timedelta(minutes=15), previous_close=1810.0))

    result = await live_quotes.get_live_quote(db_session, "MELI.US", now=OPEN)

    quote = result["quote"]
    assert quote["source"] == "realtime"
    assert quote["price"] == 1635.87
    # Same base as the watchlist sidebar: the delayed feed's previous close.
    assert quote["previous_close"] == 1810.0
    assert quote["change_pct"] == pytest.approx(1635.87 / 1810.0 - 1)
    lease = await db_session.get(LiveQuoteLease, "MELI.US")
    assert lease.expires_at == naive(OPEN + live_quotes.LEASE_TTL)


@pytest.mark.asyncio
async def test_stale_feed_falls_back_to_delayed_quote(db_session, monkeypatch):
    await _seed(db_session)
    await _stream(db_session, heartbeat=OPEN - timedelta(seconds=30), traded_at=OPEN - timedelta(seconds=40))
    monkeypatch.setattr(eodhd_client, "get_live_quotes", _delayed(1614.80, OPEN - timedelta(minutes=15)))

    quote = (await live_quotes.get_live_quote(db_session, "MELI.US", now=OPEN))["quote"]

    assert (quote["source"], quote["price"]) == ("delayed", 1614.80)


@pytest.mark.asyncio
async def test_open_session_without_any_quote_uses_daily_close_as_base(db_session, monkeypatch):
    await _seed(db_session)
    await _stream(db_session, heartbeat=OPEN, traded_at=OPEN - timedelta(seconds=2))

    async def unavailable(tickers, client=None):
        raise TimeoutError

    monkeypatch.setattr(eodhd_client, "get_live_quotes", unavailable)
    quote = (await live_quotes.get_live_quote(db_session, "MELI.US", now=OPEN))["quote"]
    assert quote["source"] == "realtime"
    assert quote["previous_close"] == pytest.approx(1810.15)

    no_stream = (await live_quotes.get_live_quote(db_session, "MELI.US", now=OPEN + timedelta(minutes=5)))
    assert no_stream["quote"] is None


@pytest.mark.asyncio
async def test_after_close_serves_official_close_from_the_eod_bar(db_session, monkeypatch):
    await _seed(db_session)
    eod_calls: list[str] = []
    monkeypatch.setattr(eodhd_client, "get_eod_historical_data", _eod(None, eod_calls))
    # As observed on 2026-10-27: past the bell the delayed feed still showed 1680.72, not the close.
    monkeypatch.setattr(eodhd_client, "get_live_quotes", _delayed(1680.72, CLOSE))
    await _stream(db_session, heartbeat=CLOSE, traded_at=CLOSE - timedelta(seconds=3), price=1681.11)

    before = (await live_quotes.get_live_quote(db_session, "MELI.US", now=CLOSE + timedelta(minutes=16)))["quote"]
    assert (before["source"], before["price"]) == ("last", 1681.11)
    # Asked at most once a minute until EODHD publishes the bar.
    await live_quotes.get_live_quote(db_session, "MELI.US", now=CLOSE + timedelta(minutes=16, seconds=30))
    assert eod_calls == ["MELI.US"]

    monkeypatch.setattr(eodhd_client, "get_eod_historical_data", _eod(1680.51, eod_calls))
    result = await live_quotes.get_live_quote(db_session, "MELI.US", now=CLOSE + timedelta(minutes=20))

    assert result["market"]["phase"] == "post"
    quote = result["quote"]
    assert (quote["source"], quote["price"], quote["as_of"], quote["session_date"]) == ("close", 1680.51, CLOSE, SESSION)
    assert quote["change_pct"] == pytest.approx(1680.51 / 1810.15 - 1)
    # Re-asked each minute while a fresh bar could still be revised, kept through a failed call,
    # then kept for good.
    async def eod_down(ticker, from_date=None, to_date=None, client=None):
        eod_calls.append(ticker)
        raise TimeoutError

    monkeypatch.setattr(eodhd_client, "get_eod_historical_data", eod_down)
    kept = (await live_quotes.get_live_quote(db_session, "MELI.US", now=CLOSE + timedelta(minutes=21)))["quote"]
    assert (kept["source"], kept["price"]) == ("close", 1680.51)
    monkeypatch.setattr(eodhd_client, "get_eod_historical_data", _eod(1680.51, eod_calls))
    await live_quotes.get_live_quote(db_session, "MELI.US", now=CLOSE + timedelta(minutes=40))
    await live_quotes.get_live_quote(db_session, "MELI.US", now=CLOSE + timedelta(minutes=50))
    assert len(eod_calls) == 4
    # No lease once the session's last trades have arrived.
    assert await db_session.get(LiveQuoteLease, "MELI.US") is None


@pytest.mark.asyncio
async def test_until_the_close_is_published_the_header_shows_the_last_regular_trade(db_session, monkeypatch):
    await _seed(db_session)
    now = CLOSE + timedelta(minutes=3)
    monkeypatch.setattr(eodhd_client, "get_live_quotes", _delayed(1638.00, CLOSE - timedelta(minutes=15)))
    await _stream(db_session, heartbeat=now, traded_at=CLOSE - timedelta(seconds=1), price=1641.25)

    quote = (await live_quotes.get_live_quote(db_session, "MELI.US", now=now))["quote"]
    assert (quote["source"], quote["price"], quote["as_of"]) == ("last", 1641.25, CLOSE - timedelta(seconds=1))

    # A trade streamed hours earlier loses to the delayed feed's later minute.
    row = await db_session.get(LiveQuote, "MELI.US")
    row.traded_at = naive(OPEN)
    await db_session.commit()
    quote = (await live_quotes.get_live_quote(db_session, "MELI.US", now=now))["quote"]
    assert (quote["source"], quote["price"], quote["as_of"]) == ("last", 1638.00, CLOSE - timedelta(minutes=15))


@pytest.mark.asyncio
async def test_delayed_feed_past_the_bell_counts_as_the_close_only_once_settled(db_session, monkeypatch):
    await _seed(db_session)

    async def eod_down(ticker, from_date=None, to_date=None, client=None):
        raise TimeoutError

    monkeypatch.setattr(eodhd_client, "get_eod_historical_data", eod_down)
    await _stream(db_session, heartbeat=CLOSE, traded_at=CLOSE - timedelta(seconds=2), price=1681.11)
    # 2026-10-27, BRK-B: stamped 16:05 the delayed feed showed an after-hours 543.07; the close was 542.99.
    monkeypatch.setattr(eodhd_client, "get_live_quotes", _delayed(1683.00, CLOSE + timedelta(minutes=5)))
    drifting = (await live_quotes.get_live_quote(db_session, "MELI.US", now=CLOSE + timedelta(minutes=20)))["quote"]
    assert (drifting["source"], drifting["price"]) == ("last", 1681.11)

    monkeypatch.setattr(eodhd_client, "get_live_quotes", _delayed(1680.51, CLOSE + timedelta(minutes=25)))
    settled = (await live_quotes.get_live_quote(db_session, "MELI.US", now=CLOSE + timedelta(minutes=40)))["quote"]
    assert (settled["source"], settled["price"]) == ("close", 1680.51)


@pytest.mark.asyncio
async def test_daily_bar_supersedes_the_close_without_provider_calls(db_session, monkeypatch):
    await _seed(db_session, last_bar=SESSION)
    calls: list[list[str]] = []

    async def fake(tickers, client=None):
        calls.append(list(tickers))
        return []

    monkeypatch.setattr(eodhd_client, "get_live_quotes", fake)
    monkeypatch.setattr(eodhd_client, "get_eod_historical_data", _eod(1640.0, calls))
    premarket = utc(2026, 10, 7, 12, 0)  # Wed 08:00 ET
    result = await live_quotes.get_live_quote(db_session, "MELI.US", now=premarket)

    assert result["market"]["phase"] == "pre"
    assert result["quote"] is None
    assert calls == []


@pytest.mark.asyncio
async def test_unknown_and_non_us_tickers_take_no_lease(db_session, monkeypatch):
    db_session.add(Ticker(ticker="0700.HK", name="Tencent"))
    await db_session.commit()
    monkeypatch.setattr(eodhd_client, "get_live_quotes", _delayed(1614.80, OPEN))

    unknown = await live_quotes.get_live_quote(db_session, "ZZZZ.US", now=OPEN)
    await live_quotes.get_live_quote(db_session, "0700.HK", now=OPEN)

    assert unknown["quote"] is None
    assert await db_session.get(LiveQuoteLease, "ZZZZ.US") is None
    assert await db_session.get(LiveQuoteLease, "0700.HK") is None


@pytest.mark.asyncio
async def test_live_quote_endpoint_validates_and_canonicalizes(monkeypatch):
    from main import app
    from api import routers

    seen: list[str] = []

    async def fake_quote(db, ticker):
        seen.append(ticker)
        return {"ticker": ticker, "market": us_market_status(OPEN), "quote": None}

    monkeypatch.setattr(routers, "get_live_quote", fake_quote)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.get("/api/v1/quotes/live", params={"ticker": " meli "})
        invalid = await client.get("/api/v1/quotes/live", params={"ticker": "MELI;DROP"})

    assert ok.status_code == 200
    assert ok.json()["quote"] is None
    assert seen == ["MELI.US"]
    assert invalid.status_code == 422
