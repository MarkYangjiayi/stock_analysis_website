import collections
import logging
import time

from core.config import settings
from services.notifications import NotificationManager

logger = logging.getLogger(__name__)

# Sliding window to store prices: { "AAPL": deque(maxlen=60) }
price_window = collections.defaultdict(collections.deque)

# Cooldown cache to track last alert time: { "AAPL": 1700000000.0 }
cooldown_cache = {}


def monitor_symbols() -> list[str]:
    """WebSocket symbols the intraday move alert watches, or none when it is off."""
    if not settings.ENABLE_WS_MONITOR:
        return []
    return [symbol.strip().upper() for symbol in settings.WS_SYMBOLS.split(",") if symbol.strip()]


class WSMonitor:
    """Intraday move alerts on WS_SYMBOLS.

    Ticks arrive from the worker's single EODHD WebSocket in
    ``services/live_quotes.py``, which subscribes these symbols whenever the
    monitor is enabled.
    """

    async def process_tick(self, ticker: str, new_price: float):
        """Detects anomalies using a sliding window approach."""
        if new_price <= 0:
            return
        current_time = time.time()
        window = price_window[ticker]
        window.append((current_time, new_price))
        cutoff = current_time - settings.WS_WINDOW_SECONDS
        while window and window[0][0] < cutoff:
            window.popleft()

        # We need at least 2 data points to calculate change
        if len(window) < 2:
            return

        old_price = window[0][1]  # Oldest price in the time window
        if old_price == 0:
            return

        change_pct = (new_price - old_price) / old_price

        # Check if absolute change >= 1.5%
        if abs(change_pct) >= settings.WS_ALERT_THRESHOLD:
            last_alert_time = cooldown_cache.get(ticker, 0)

            # Cooldown is 15 minutes (900 seconds)
            if current_time - last_alert_time < settings.WS_COOLDOWN_SECONDS:
                return  # Skip, in cooldown

            # Trigger alert
            logger.warning(f"Intraday Anomaly detected for {ticker}: {change_pct*100:.2f}%")
            content = f"**{ticker}** 盘中出现剧烈波动！\n\n当前价格：**${new_price}**\n短线波幅：**{change_pct*100:.2f}%**"
            await NotificationManager.broadcast(
                title="🚨 盘中剧烈异动警报",
                content=content
            )

            # Update cooldown cache
            cooldown_cache[ticker] = current_time

ws_monitor = WSMonitor()
