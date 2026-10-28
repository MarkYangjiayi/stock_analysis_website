"""Real-time trade prices for the analysis page header.

EODHD's REST quote is delayed 15 minutes on every plan; its US WebSocket streams
trades within seconds. The WebSocket allowance is 50 symbols per API token across
every socket, so the worker holds the only connection and allocates it:

* The API records a one-minute lease for each US ticker an analysis page shows
  (``record_lease``), renewed by the page's 3-second poll.
* During the regular session the hub subscribes leased tickers, newest lease
  first, up to ``LIVE_QUOTE_MAX_SYMBOLS``. WS_SYMBOLS are pinned while the
  intraday move monitor is on. A symbol stays subscribed for a grace period
  after its lease lapses, and the socket closes after five idle minutes or once
  the session's last trades have arrived after the bell.
* The latest regular-session trade per ticker goes to ``live_quotes`` about once
  a second; ``live_quote_feed`` carries the connection heartbeat.

``get_live_quote`` serves the header: the streamed trade while the session is
open, else the delayed quote. After the close it serves the session's last
regular trade until EODHD's EOD bar for the session carries the official close,
then that close until the daily bar for it lands.
"""

import asyncio
import json
import logging
import math
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Literal, Optional

import websockets
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.time_utils import utc_now
from core.trading_calendar import NEW_YORK, latest_completed_us_session, us_market_status, us_session_bounds
from database import async_session_maker
from models import DailyPrice, LiveQuote, LiveQuoteFeed, LiveQuoteLease, Ticker
from services import eodhd_client, watchlist_quotes
from services.ws_monitor import monitor_symbols, ws_monitor


logger = logging.getLogger(__name__)

WS_URL = "wss://ws.eodhistoricaldata.com/ws/us"
FEED_ID = 1
LEASE_TTL = timedelta(seconds=60)
# Keep a symbol after its lease lapses so flipping between pages does not churn subscriptions.
UNSUBSCRIBE_GRACE = timedelta(minutes=2)
IDLE_DISCONNECT = timedelta(minutes=5)
# Stay connected this long after the close: the session's last trades arrive up to six seconds late.
CLOSE_TAIL = timedelta(minutes=5)
# EODHD publishes the session's EOD bar, with the official close, about 20 minutes after the bell.
OFFICIAL_CLOSE_RETRY = timedelta(seconds=60)
# The delayed feed passes the bell about 15 minutes late and keeps picking up after-hours
# prints for another 15-20 (2026-10-27); only after that does it hold the official close.
DELAYED_CLOSE_SETTLE = timedelta(minutes=35)
FEED_STALE_AFTER = timedelta(seconds=15)
HEARTBEAT_SECONDS = 5.0
POLL_SECONDS = 1.0
STABLE_CONNECTION_SECONDS = 30
REJECTED_RETRY_SECONDS = 60.0
MAX_RECONNECT_DELAY = 60

_US_BASE = re.compile(r"[A-Z0-9]+(?:-[A-Z0-9]+)?")
# Per ticker: the session, when its EOD bar was last asked for, and its close once published.
_official_closes: dict[str, tuple[date, datetime, Optional[float]]] = {}

QuoteSource = Literal["realtime", "delayed", "last", "close"]


def ws_symbol(ticker: str) -> Optional[str]:
    """EODHD WebSocket symbol for a site ticker (``BRK-B.US`` → ``BRK.B``); None outside the US feed."""
    value = ticker.strip().upper()
    if not value.endswith(".US") or not _US_BASE.fullmatch(value[:-3]):
        return None
    return value[:-3].replace("-", ".")


def site_ticker(symbol: str) -> str:
    return f"{symbol.upper().replace('.', '-')}.US"


def _naive(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def _aware(moment: datetime) -> datetime:
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def in_live_window(now: datetime) -> bool:
    """The regular session plus the tail in which its last trades arrive."""
    status = us_market_status(now)
    if status["phase"] == "open":
        return True
    closes_at = status["closes_at"]
    return closes_at is not None and closes_at <= now < closes_at + CLOSE_TAIL


def regular_session_date(traded_at: datetime) -> Optional[date]:
    """Session of a trade printed during regular hours, else None.

    EODHD streams no closing auction print: everything stamped from the bell on is an
    extended-hours trade (2026-10-27), so the bell itself is outside the session.
    """
    session_date = traded_at.astimezone(NEW_YORK).date()
    bounds = us_session_bounds(session_date)
    if bounds is None:
        return None
    opens_at, closes_at = bounds
    return session_date if opens_at <= traded_at < closes_at else None


class LiveQuoteHub:
    """Owns the worker's EODHD WebSocket; see the module docstring."""

    def __init__(
        self,
        connect: Callable[..., Any] = websockets.connect,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self._connect = connect
        self._clock = clock
        self._latest: dict[str, tuple[float, datetime, date]] = {}
        self._dirty: set[str] = set()
        self._subscribed: set[str] = set()
        # The last subscribe batch, blamed when EODHD rejects a subscription.
        self._pending: list[str] = []
        self._rejected: dict[str, float] = {}
        self._connected = False
        self._error: Optional[str] = None
        self._written_status: Optional[tuple[Any, ...]] = None
        self._heartbeat_at = 0.0

    async def desired_symbols(self, now: datetime) -> list[str]:
        """Pinned monitor symbols, then leased tickers newest first, within the symbol budget."""
        symbols = monitor_symbols()
        if settings.LIVE_QUOTES_ENABLED and in_live_window(now):
            async with async_session_maker() as db:
                leased = (await db.execute(
                    select(LiveQuoteLease.ticker)
                    .where(LiveQuoteLease.expires_at > _naive(now - UNSUBSCRIBE_GRACE))
                    .order_by(LiveQuoteLease.expires_at.desc())
                )).scalars().all()
            for ticker in leased:
                symbol = ws_symbol(ticker)
                if symbol and symbol not in symbols:
                    symbols.append(symbol)
        retry_at = time.monotonic()
        allowed = [symbol for symbol in symbols if self._rejected.get(symbol, 0.0) <= retry_at]
        return allowed[:settings.LIVE_QUOTE_MAX_SYMBOLS]

    async def on_trade(self, message: dict[str, Any]) -> None:
        symbol = str(message.get("s") or "").upper()
        try:
            price = float(message["p"])
            traded_at = datetime.fromtimestamp(float(message["t"]) / 1000, timezone.utc)
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            return
        if not symbol or not math.isfinite(price) or price <= 0:
            return
        if symbol in monitor_symbols():
            try:
                await ws_monitor.process_tick(symbol, price)
            except Exception as exc:  # A failed alert must not drop the stream.
                logger.warning("Move monitor failed on %s (%s)", symbol, type(exc).__name__)
        session_date = regular_session_date(traded_at)
        if session_date is None:
            return
        ticker = site_ticker(symbol)
        current = self._latest.get(ticker)
        # Late-reported trades arrive out of order; keep the newest print.
        if current is not None and current[1] >= traded_at:
            return
        self._latest[ticker] = (price, traded_at, session_date)
        self._dirty.add(ticker)

    def on_status(self, message: dict[str, Any]) -> None:
        code = message.get("status_code")
        if code == 200:
            return
        logger.warning("Live quote WebSocket status %s: %s", code, message.get("message"))
        self._error = str(message.get("message") or code)[:200]
        if code == 422 and self._pending:
            # Usually the token's 50-symbol allowance is used elsewhere; serve these delayed for a while.
            retry_at = time.monotonic() + REJECTED_RETRY_SECONDS
            for symbol in self._pending:
                self._rejected[symbol] = retry_at
                self._subscribed.discard(symbol)
            self._pending = []

    async def flush(self) -> None:
        """Write changed trades, and the heartbeat while connected or when the feed state changes."""
        dirty, self._dirty = self._dirty, set()
        status = (self._connected, tuple(sorted(self._subscribed)), self._error)
        status_due = status != self._written_status or (
            self._connected and time.monotonic() - self._heartbeat_at >= HEARTBEAT_SECONDS
        )
        if not dirty and not status_due:
            return
        received_at = utc_now()
        try:
            async with async_session_maker() as db:
                if dirty:
                    rows = [
                        {
                            "ticker": ticker,
                            "price": self._latest[ticker][0],
                            "traded_at": _naive(self._latest[ticker][1]),
                            "session_date": self._latest[ticker][2],
                            "received_at": received_at,
                        }
                        for ticker in sorted(dirty)
                    ]
                    stmt = insert(LiveQuote).values(rows)
                    await db.execute(stmt.on_conflict_do_update(
                        index_elements=[LiveQuote.ticker],
                        set_={column: stmt.excluded[column] for column in ("price", "traded_at", "session_date", "received_at")},
                    ))
                if status_due:
                    values = {
                        "connected": self._connected,
                        "tickers": [site_ticker(symbol) for symbol in status[1]],
                        "heartbeat_at": received_at,
                        "error": self._error,
                    }
                    stmt = insert(LiveQuoteFeed).values(id=FEED_ID, **values)
                    await db.execute(stmt.on_conflict_do_update(index_elements=[LiveQuoteFeed.id], set_=values))
                await db.commit()
        except Exception:
            self._dirty |= dirty
            raise
        if status_due:
            self._written_status = status
            self._heartbeat_at = time.monotonic()

    async def _reconcile(self, ws: Any, desired: list[str]) -> None:
        wanted = set(desired)
        dropped = sorted(self._subscribed - wanted)
        added = [symbol for symbol in desired if symbol not in self._subscribed]
        if dropped:
            await ws.send(json.dumps({"action": "unsubscribe", "symbols": ",".join(dropped)}))
            self._subscribed -= set(dropped)
        if added:
            await ws.send(json.dumps({"action": "subscribe", "symbols": ",".join(added)}))
            self._subscribed |= set(added)
            self._pending = added
        if dropped or added:
            logger.info("Live quote WebSocket streams %s", ",".join(sorted(self._subscribed)) or "nothing")

    async def _read(self, ws: Any) -> None:
        async for raw in ws:
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(message, dict):
                continue
            if "status_code" in message:
                self.on_status(message)
            else:
                await self.on_trade(message)

    async def _session(self) -> bool:
        """Run one connection; True when it ended because nothing needs streaming."""
        async with self._connect(f"{WS_URL}?api_token={settings.EODHD_API_KEY}") as ws:
            self._connected, self._error = True, None
            logger.info("Live quote WebSocket connected")
            reader = asyncio.create_task(self._read(ws))
            idle_since: Optional[datetime] = None
            try:
                while not reader.done():
                    now = self._clock()
                    desired = await self.desired_symbols(now)
                    if desired:
                        idle_since = None
                    elif not in_live_window(now):
                        return True
                    else:
                        idle_since = idle_since or now
                        if now - idle_since >= IDLE_DISCONNECT:
                            return True
                    await self._reconcile(ws, desired)
                    await self._flush_quietly()
                    await asyncio.wait({reader}, timeout=POLL_SECONDS)
                reader.result()
                return False
            finally:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
                self._connected = False
                self._subscribed.clear()
                self._pending = []

    async def run(self) -> None:
        """Connect while something needs streaming; back off after short-lived connections."""
        logger.info("Starting live quote hub")
        reconnect_delay = 1
        while True:
            if not await self.desired_symbols(self._clock()):
                await self._flush_quietly()
                await asyncio.sleep(POLL_SECONDS)
                continue
            started = time.monotonic()
            try:
                idle = await self._session()
                failure = None if idle else "closed by server"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure = f"{type(exc).__name__}: {exc}"
                if settings.EODHD_API_KEY:
                    # The token rides in the socket URL; keep it out of logs and the feed row.
                    failure = failure.replace(settings.EODHD_API_KEY, "***")
                failure = failure[:200]
            if failure is None:
                logger.info("Live quote WebSocket closed; nothing to stream")
                await self._flush_quietly()
                reconnect_delay = 1
                continue
            if time.monotonic() - started >= STABLE_CONNECTION_SECONDS:
                reconnect_delay = 1
            self._error = failure
            logger.warning("Live quote WebSocket disconnected (%s); reconnecting in %ss", failure, reconnect_delay)
            await self._flush_quietly()
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, MAX_RECONNECT_DELAY)

    async def _flush_quietly(self) -> None:
        try:
            await self.flush()
        except Exception as exc:  # The next loop retries; a busy database must not kill the hub.
            logger.warning("Live quote flush failed (%s)", type(exc).__name__)


live_quote_hub = LiveQuoteHub()


async def record_lease(db: AsyncSession, ticker: str, now: datetime) -> None:
    expires_at = _naive(now + LEASE_TTL)
    stmt = insert(LiveQuoteLease).values(ticker=ticker, expires_at=expires_at)
    await db.execute(stmt.on_conflict_do_update(index_elements=[LiveQuoteLease.ticker], set_={"expires_at": expires_at}))
    await db.commit()


def _quote(
    price: float,
    as_of: datetime,
    session_date: date,
    previous_close: Optional[float],
    source: QuoteSource,
) -> dict[str, Any]:
    change = price - previous_close if previous_close else None
    return {
        "price": price,
        "previous_close": previous_close,
        "change": change,
        "change_pct": change / previous_close if change is not None else None,
        "as_of": as_of,
        "session_date": session_date,
        "source": source,
    }


async def _previous_close(
    db: AsyncSession,
    ticker: str,
    session_date: date,
    delayed: Optional[dict[str, Any]],
) -> Optional[float]:
    """Base for the day's change: the delayed feed's previous close, as on the watchlist, else the last raw close."""
    if delayed is not None and delayed["session_date"] == session_date and delayed["previous_close"]:
        return delayed["previous_close"]
    close = await db.scalar(
        select(DailyPrice.close)
        .where(DailyPrice.ticker == ticker, DailyPrice.date < session_date, DailyPrice.close.is_not(None))
        .order_by(DailyPrice.date.desc())
        .limit(1)
    )
    return float(close) if close is not None and close > 0 else None


async def _streamed_trade(db: AsyncSession, ticker: str, now: datetime) -> Optional[LiveQuote]:
    feed = await db.get(LiveQuoteFeed, FEED_ID)
    if feed is None or not feed.connected or ticker not in (feed.tickers or []):
        return None
    if now - _aware(feed.heartbeat_at) > FEED_STALE_AFTER:
        return None
    return await db.get(LiveQuote, ticker)


async def _session_quote(db: AsyncSession, ticker: str, status: dict[str, Any], now: datetime) -> Optional[dict[str, Any]]:
    session_date = status["session_date"]
    delayed = await watchlist_quotes.get_delayed_quote(ticker, now)
    previous_close = await _previous_close(db, ticker, session_date, delayed)
    trade = await _streamed_trade(db, ticker, now)
    if trade is not None and trade.session_date == session_date:
        return _quote(trade.price, _aware(trade.traded_at), session_date, previous_close, "realtime")
    if delayed is not None and delayed["session_date"] == session_date and delayed["as_of"] >= status["opens_at"]:
        return _quote(delayed["price"], delayed["as_of"], session_date, previous_close, "delayed")
    return None


async def _official_close(ticker: str, session_date: date, closes_at: datetime, now: datetime) -> Optional[float]:
    """The session's official close from its EOD bar.

    Asked once a minute until the delayed feed would have settled too, so a freshly
    published bar that EODHD revises is picked up; after that the close is kept.
    """
    cached = _official_closes.get(ticker)
    previous = cached[2] if cached is not None and cached[0] == session_date else None
    if cached is not None and cached[0] == session_date:
        fetched_at = cached[1]
        if now - fetched_at < OFFICIAL_CLOSE_RETRY or (previous is not None and fetched_at >= closes_at + DELAYED_CLOSE_SETTLE):
            return previous
    close = None
    try:
        rows = await eodhd_client.get_eod_historical_data(ticker, session_date.isoformat(), session_date.isoformat())
    except Exception as exc:  # Keep what was published; the next poll asks again.
        logger.warning("EOD close unavailable for %s (%s)", ticker, type(exc).__name__)
        rows = None
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("date") == session_date.isoformat():
            try:
                value = float(row.get("close"))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value > 0:
                close = value
    close = close if close is not None else previous
    _official_closes[ticker] = (session_date, now, close)
    return close


async def _closing_quote(db: AsyncSession, ticker: str, status: dict[str, Any], now: datetime) -> Optional[dict[str, Any]]:
    """The last completed session's close, until the daily bar for that session carries it."""
    today = now.astimezone(NEW_YORK).date()
    closes_today = status["closes_at"]
    session_date = today if closes_today is not None and now >= closes_today else latest_completed_us_session(today)
    has_bar = await db.scalar(
        select(DailyPrice.id).where(DailyPrice.ticker == ticker, DailyPrice.date == session_date).limit(1)
    )
    bounds = us_session_bounds(session_date)
    if has_bar is not None or bounds is None:
        return None
    closes_at = bounds[1]
    delayed = await watchlist_quotes.get_delayed_quote(ticker, now)
    if delayed is not None and delayed["session_date"] != session_date:
        delayed = None
    previous_close = await _previous_close(db, ticker, session_date, delayed)
    official = await _official_close(ticker, session_date, closes_at, now)
    if official is not None:
        return _quote(official, closes_at, session_date, previous_close, "close")
    if delayed is not None and delayed["as_of"] >= closes_at and now >= closes_at + DELAYED_CLOSE_SETTLE:
        return _quote(delayed["price"], delayed["as_of"], session_date, previous_close, "close")
    # Until the close is known, the newest regular-session price: the streamed trade, or the
    # delayed feed's last minute before the bell when the ticker was not streamed.
    last: list[tuple[datetime, float]] = []
    trade = await db.get(LiveQuote, ticker)
    if trade is not None and trade.session_date == session_date and _aware(trade.traded_at) < closes_at:
        last.append((_aware(trade.traded_at), trade.price))
    if delayed is not None and delayed["as_of"] < closes_at:
        last.append((delayed["as_of"], delayed["price"]))
    if not last:
        return None
    as_of, price = max(last)
    return _quote(price, as_of, session_date, previous_close, "last")


async def get_live_quote(db: AsyncSession, ticker: str, now: Optional[datetime] = None) -> dict[str, Any]:
    """Header price: the streamed trade in the session, else the delayed quote; after it, the last trade, then the close."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    status = us_market_status(now)
    known = await db.get(Ticker, ticker) is not None
    if settings.LIVE_QUOTES_ENABLED and known and ws_symbol(ticker) and in_live_window(now):
        await record_lease(db, ticker, now)
    quote = None
    if known:
        if status["phase"] == "open":
            quote = await _session_quote(db, ticker, status, now)
        else:
            quote = await _closing_quote(db, ticker, status, now)
    return {"ticker": ticker, "market": status, "quote": quote}
