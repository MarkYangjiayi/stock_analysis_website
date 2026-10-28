import asyncio
import base64
import hashlib
import hmac
import ipaddress
import secrets
import time
from collections import defaultdict, deque
from typing import Deque, DefaultDict, Optional, Tuple, Union

from fastapi import Header, HTTPException, Request, Response, status
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


ADMIN_SESSION_COOKIE = "finbrain_admin_session"
_ADMIN_SESSION_VERSION = "v1"


def _admin_session_signature(payload: str) -> str:
    # Keyed by the admin key, so rotating it ends every browser session.
    key = hmac.new(settings.ADMIN_API_KEY.encode(), b"finbrain-admin-session", hashlib.sha256).digest()
    digest = hmac.new(key, payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def issue_admin_session(now: Optional[float] = None) -> Tuple[str, int]:
    """Return a signed session token and its expiry as a Unix timestamp."""
    expires_at = int(time.time() if now is None else now) + settings.ADMIN_SESSION_DAYS * 86400
    payload = f"{_ADMIN_SESSION_VERSION}.{expires_at}"
    return f"{payload}.{_admin_session_signature(payload)}", expires_at


def admin_session_is_valid(token: str, now: Optional[float] = None) -> bool:
    if not settings.ADMIN_API_KEY and settings.ENVIRONMENT.lower() == "production":
        return False
    try:
        version, expires, signature = token.split(".")
        expires_at = int(expires)
    except ValueError:
        return False
    if version != _ADMIN_SESSION_VERSION:
        return False
    if not secrets.compare_digest(signature, _admin_session_signature(f"{version}.{expires}")):
        return False
    return expires_at > (time.time() if now is None else now)


def has_admin_session(request: Request) -> bool:
    return admin_session_is_valid(request.cookies.get(ADMIN_SESSION_COOKIE, ""))


def _admin_session_cookie_options() -> dict:
    # HttpOnly keeps the session away from page scripts; Strict keeps other
    # sites from riding it. The cookie is only sent to the API.
    return {
        "path": "/api",
        "httponly": True,
        "samesite": "strict",
        "secure": settings.ENVIRONMENT.lower() == "production",
    }


def set_admin_session_cookie(response: Response) -> int:
    token, expires_at = issue_admin_session()
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        token,
        max_age=settings.ADMIN_SESSION_DAYS * 86400,
        **_admin_session_cookie_options(),
    )
    return expires_at


def clear_admin_session_cookie(response: Response) -> None:
    response.delete_cookie(ADMIN_SESSION_COOKIE, **_admin_session_cookie_options())


def _verify_admin_key(request: Optional[Request], x_api_key: str) -> None:
    client = _client_identifier(request) if request is not None else "unknown"
    admin_auth_limiter.check(client)
    # A browser session stands in for the key; an explicit key always wins.
    if not x_api_key and request is not None and has_admin_session(request):
        return
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


async def has_optional_admin_access(request: Request) -> bool:
    """Whether a public endpoint may add personal data to its response.

    Anonymous requests are fine; a wrong key still fails rather than silently
    falling back to the public view.
    """
    api_key = request.headers.get("x-api-key", "")
    if not api_key and not has_admin_session(request):
        return False
    await require_admin_api_key(request, api_key)
    return True


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
        personal = scope.get("path", "").startswith(("/api/personal/", "/api/auth/"))

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
_live_quote_limiter = SlidingWindowRateLimiter(settings.LIVE_QUOTE_REQUESTS_PER_MINUTE)


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


async def limit_live_quote_requests(request: Request) -> None:
    await _live_quote_limiter.check(_client_identifier(request))
