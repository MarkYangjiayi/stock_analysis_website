import asyncio
import ipaddress
import secrets
import time
from collections import defaultdict, deque
from typing import Deque, DefaultDict, Union

from fastapi import Header, HTTPException, Request, status

from core.config import settings


async def require_admin_api_key(x_api_key: str = Header(default="")) -> None:
    """Protect state-changing and operational endpoints.

    Development remains convenient when no key is configured. Production
    stays available in read-only mode while admin operations fail closed.
    """
    if not settings.ADMIN_API_KEY:
        if settings.ENVIRONMENT.lower() == "production":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Admin operations are disabled",
            )
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.ADMIN_API_KEY):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


async def require_configured_admin_api_key(
    x_api_key: str = Header(default=""),
) -> None:
    """Require an explicitly configured key even outside production.

    Cost-bearing, user-triggered AI operations use this stricter guard; an
    empty development key must never turn them into anonymous endpoints.
    """
    if not settings.ADMIN_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin operations are disabled",
        )
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.ADMIN_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )


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


def _is_trusted_proxy(
    address: Union[ipaddress.IPv4Address, ipaddress.IPv6Address],
) -> bool:
    for value in settings.trusted_proxy_ips:
        try:
            if address in ipaddress.ip_network(value, strict=False):
                return True
        except ValueError:
            continue
    return False


def _client_identifier(request: Request) -> str:
    """Resolve a client IP while trusting forwarding headers only from known proxies."""
    peer = request.client.host if request.client else "unknown"
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer

    if not _is_trusted_proxy(peer_ip):
        return peer

    forwarded_for = request.headers.get("x-forwarded-for", "")
    if not forwarded_for:
        return peer

    # Trusted proxies append their observed upstream address to the right side
    # of X-Forwarded-For. Walk from that trusted edge and stop at the first
    # untrusted hop, ignoring any client-supplied addresses farther left.
    for raw_hop in reversed(forwarded_for.split(",")):
        try:
            hop = ipaddress.ip_address(raw_hop.strip())
        except ValueError:
            return peer
        if not _is_trusted_proxy(hop):
            return str(hop)
    return peer


async def limit_expensive_requests(request: Request) -> None:
    await _expensive_limiter.check(_client_identifier(request))
