import asyncio
import secrets
import time
from collections import defaultdict, deque
from typing import Deque, DefaultDict

from fastapi import Header, HTTPException, Request, status

from core.config import settings


async def require_admin_api_key(x_api_key: str = Header(default="")) -> None:
    """Protect state-changing and operational endpoints.

    Development remains convenient when no key is configured. Production
    configuration validation refuses to start without a key.
    """
    if not settings.ADMIN_API_KEY and settings.ENVIRONMENT.lower() != "production":
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.ADMIN_API_KEY):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: int = 60):
        self.limit = limit
        self.window_seconds = window_seconds
        self._requests: DefaultDict[str, Deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()
        self._next_cleanup = 0.0

    async def check(self, key: str) -> None:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        async with self._lock:
            if now >= self._next_cleanup:
                stale_keys = []
                for request_key, request_times in self._requests.items():
                    while request_times and request_times[0] <= cutoff:
                        request_times.popleft()
                    if not request_times:
                        stale_keys.append(request_key)
                for request_key in stale_keys:
                    del self._requests[request_key]
                self._next_cleanup = now + self.window_seconds

            timestamps = self._requests[key]
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            if len(timestamps) >= self.limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded; retry later",
                )
            timestamps.append(now)


_expensive_limiter = SlidingWindowRateLimiter(settings.EXPENSIVE_REQUESTS_PER_MINUTE)


async def limit_expensive_requests(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    await _expensive_limiter.check(client)
