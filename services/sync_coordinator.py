import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from threading import Lock


@dataclass
class _TickerLockEntry:
    lock: asyncio.Lock
    users: int = 0


_ticker_locks: dict[str, _TickerLockEntry] = {}
_ticker_locks_guard = Lock()


@asynccontextmanager
async def ticker_sync_lock(ticker: str):
    key = ticker.upper()
    with _ticker_locks_guard:
        entry = _ticker_locks.get(key)
        if entry is None:
            entry = _TickerLockEntry(lock=asyncio.Lock())
            _ticker_locks[key] = entry
        entry.users += 1

    try:
        async with entry.lock:
            yield
    finally:
        with _ticker_locks_guard:
            entry.users -= 1
            if entry.users == 0:
                _ticker_locks.pop(key, None)
