import asyncio
import websockets
import json
import time
import collections
import logging

from core.config import settings
from services.notifications import NotificationManager

logger = logging.getLogger(__name__)

# Sliding window to store prices: { "AAPL": deque(maxlen=60) }
price_window = collections.defaultdict(lambda: collections.deque(maxlen=60))

# Cooldown cache to track last alert time: { "AAPL": 1700000000.0 }
cooldown_cache = {}

class WSMonitor:
    def __init__(self):
        self.api_token = settings.EODHD_API_KEY
        self.ws_url = f"wss://ws.eodhistoricaldata.com/ws/us?api_token={self.api_token}"
        
    async def connect(self):
        """Connects to the WebSocket server and sends the subscription message."""
        try:
            async with websockets.connect(self.ws_url) as websocket:
                logger.info("Connected to EODHD WebSocket.")
                subscribe_msg = {
                    "action": "subscribe",
                    "symbols": "AAPL, NVDA, TSLA, ASTS"
                }
                await websocket.send(json.dumps(subscribe_msg))
                logger.info(f"Subscribed to symbols: {subscribe_msg['symbols']}")
                
                await self.listen(websocket)
        except Exception as e:
            logger.error(f"WebSocket connection error: {e}")
            raise e
            
    async def listen(self, websocket):
        """Listens for incoming messages and processes them."""
        async for message in websocket:
            try:
                data = json.loads(message)
                # Parse JSON, extract s (ticker) and p (price)
                if "s" in data and "p" in data:
                    ticker = data["s"]
                    new_price = float(data["p"])
                    await self._process_tick(ticker, new_price)
            except json.JSONDecodeError:
                continue
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                
    async def _process_tick(self, ticker: str, new_price: float):
        """Detects anomalies using a sliding window approach."""
        window = price_window[ticker]
        window.append(new_price)
        
        # We need at least 2 data points to calculate change
        if len(window) < 2:
            return
            
        old_price = window[0]  # Oldest price in the window
        if old_price == 0:
            return
            
        change_pct = (new_price - old_price) / old_price
        
        # Check if absolute change >= 1.5%
        if abs(change_pct) >= 0.015:
            current_time = time.time()
            last_alert_time = cooldown_cache.get(ticker, 0)
            
            # Cooldown is 15 minutes (900 seconds)
            if current_time - last_alert_time < 900:
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

    async def start(self):
        """Main loop that keeps the WebSocket connection alive."""
        logger.info("Starting WebSocket Monitor daemon...")
        while True:
            try:
                await self.connect()
            except Exception as e:
                logger.error(f"WebSocket disconnected. Reconnecting in 5 seconds... Error: {e}")
                await asyncio.sleep(5)

ws_monitor = WSMonitor()
