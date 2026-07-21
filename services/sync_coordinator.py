import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager


_ticker_locks = defaultdict(asyncio.Lock)


@asynccontextmanager
async def ticker_sync_lock(ticker: str):
    lock = _ticker_locks[ticker]
    async with lock:
        yield
