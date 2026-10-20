import asyncio
import ipaddress
import secrets
import time
from collections import defaultdict, deque
from typing import Deque, DefaultDict, Optional, Union

from fastapi import Header, HTTPException, Request, status
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.config import settings


class AuthFailureLimiter:
    """Lock a client out after repeated wrong admin keys.

    While locked out even the right key is refused, so a guess cannot be
    confirmed. Limits are read from settings on every check.
    """

    def __init__(self) -> None:
        self._failures: DefaultDict[str, Deque[float]] = defaultdict(deque)

    def _recent(self, client: str, now: float) -> Deque[float]:
        failures = self._failures[client]
        cutoff = now - settings.ADMIN_AUTH_FAILURE_WINDOW_SECONDS
        while failures and failures[0] <= cutoff:
            failures.popleft()
        return failures

    def check(self, client: str) -> None:
        now = time.monotonic()
        failures = self._recent(client, now)
        if not failures:
            self._failures.pop(client, None)
        elif len(failures) >= settings.ADMIN_AUTH_MAX_FAILURES:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed attempts; retry later",
            )

    def record_failure(self, client: str) -> None:
        now = time.monotonic()
        self._recent(client, now).append(now)
        if len(self._failures) > 1000:
            for stale in [key for key, values in self._failures.items() if not self._recent(key, now)]:
                del self._failures[stale]

    def reset(self) -> None:
        self._failures.clear()


admin_auth_limiter = AuthFailureLimiter()


def _verify_admin_key(request: Optional[Request], x_api_key: str) -> None:
    client = _client_identifier(request) if request is not None else "unknown"
    admin_auth_limiter.check(client)
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.ADMIN_API_KEY):
        # A missing key is a locked page, not a guess.
        if x_api_key:
            admin_auth_limiter.record_failure(client)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


async def require_admin_api_key(request: Request, x_api_key: str = Header(default="")) -> None:
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
    _verify_admin_key(request, x_api_key)


async def require_configured_admin_api_key(
    request: Request,
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
    _verify_admin_key(request, x_api_key)


class SecurityHeadersMiddleware:
    """Baseline browser protections on every API response.

    Pure ASGI so streamed responses (the AI brief) are not buffered. Personal
    data is never stored by browsers or proxies.
    """

    HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
    }

    def __init__(self, app: ASGIApp, *, hsts: bool = False) -> None:
        self.app = app
        self.headers = dict(self.HEADERS)
        if hsts:
            self.headers["Strict-Transport-Security"] = "max-age=31536000"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        personal = scope.get("path", "").startswith("/api/personal/")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self.headers.items():
                    headers.setdefault(name, value)
                if personal:
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_headers)


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
_watchlist_quote_limiter = SlidingWindowRateLimiter(settings.WATCHLIST_QUOTE_REQUESTS_PER_MINUTE)


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


async def limit_watchlist_quote_requests(request: Request) -> None:
    await _watchlist_quote_limiter.check(_client_identifier(request))
