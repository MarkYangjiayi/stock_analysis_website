import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from core import security
from core.security import SecurityHeadersMiddleware, admin_auth_limiter

ADMIN = {"X-API-Key": "test-secret"}


def test_repeated_wrong_keys_lock_the_client_out_even_with_the_right_key(monkeypatch):
    from main import app

    monkeypatch.setattr(security.settings, "ADMIN_AUTH_MAX_FAILURES", 3)
    with TestClient(app) as client:
        for _ in range(3):
            assert client.get("/api/personal/watchlist", headers={"X-API-Key": "guess"}).status_code == 401
        locked = client.get("/api/personal/watchlist", headers=ADMIN)
        assert locked.status_code == 429
        assert locked.json()["detail"] == "Too many failed attempts; retry later"
        # The lockout covers every admin-key check, including optional ones.
        assert client.get("/api/v1/themes", headers=ADMIN).status_code == 429


def test_requests_without_a_key_are_not_counted_as_guesses(monkeypatch):
    from main import app

    monkeypatch.setattr(security.settings, "ADMIN_AUTH_MAX_FAILURES", 2)
    with TestClient(app) as client:
        for _ in range(5):
            assert client.get("/api/personal/watchlist").status_code == 401
        assert client.get("/api/personal/watchlist", headers=ADMIN).status_code == 200


def test_lockout_is_per_client_and_ends_after_the_window(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(security.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(security.settings, "ADMIN_AUTH_MAX_FAILURES", 2)
    monkeypatch.setattr(security.settings, "ADMIN_AUTH_FAILURE_WINDOW_SECONDS", 900)
    admin_auth_limiter.record_failure("203.0.113.5")
    admin_auth_limiter.record_failure("203.0.113.5")

    with pytest.raises(Exception) as locked:
        admin_auth_limiter.check("203.0.113.5")
    assert locked.value.status_code == 429
    admin_auth_limiter.check("198.51.100.7")

    now[0] += 901
    admin_auth_limiter.check("203.0.113.5")
    assert "203.0.113.5" not in admin_auth_limiter._failures


def test_api_responses_carry_security_headers_and_personal_data_is_not_cached():
    from main import app

    with TestClient(app) as client:
        public = client.get("/health/live")
        personal = client.get("/api/personal/watchlist", headers=ADMIN)
    for response in (public, personal):
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert personal.headers["Cache-Control"] == "no-store"
    assert "no-store" not in public.headers.get("Cache-Control", "")
    # Tests run outside production, so no HSTS.
    assert "Strict-Transport-Security" not in public.headers


def test_hsts_is_added_in_production_and_streams_are_not_buffered():
    app = FastAPI()

    @app.get("/stream")
    async def stream():
        async def chunks():
            yield "first "
            yield "second"
        return StreamingResponse(chunks(), media_type="text/plain")

    app.add_middleware(SecurityHeadersMiddleware, hsts=True)
    with TestClient(app) as client:
        response = client.get("/stream")
    assert response.text == "first second"
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_admin_key_is_exchanged_for_an_httponly_session_cookie():
    from main import app

    with TestClient(app) as client:
        assert client.get("/api/auth/session").json()["authenticated"] is False
        login = client.post("/api/auth/session", headers=ADMIN)
        assert login.status_code == 200
        assert login.json()["authenticated"] is True
        cookie = login.headers["set-cookie"]
        assert cookie.startswith(f"{security.ADMIN_SESSION_COOKIE}=")
        for attribute in ("HttpOnly", "Path=/api", "SameSite=strict", "Max-Age=2592000"):
            assert attribute in cookie
        # Outside production the cookie works over plain http for local dev.
        assert "Secure" not in cookie
        assert login.headers["Cache-Control"] == "no-store"

        # The browser no longer sends the key; the cookie alone unlocks admin routes.
        assert client.get("/api/personal/watchlist").status_code == 200

        status = client.get("/api/auth/session")
        assert status.json()["authenticated"] is True
        # Opening the site renews the session.
        assert security.ADMIN_SESSION_COOKIE in status.headers["set-cookie"]

        logout = client.delete("/api/auth/session")
        assert logout.json()["authenticated"] is False
        assert client.get("/api/personal/watchlist").status_code == 401


def test_session_login_counts_wrong_keys_and_needs_a_key(monkeypatch):
    from main import app

    monkeypatch.setattr(security.settings, "ADMIN_AUTH_MAX_FAILURES", 2)
    with TestClient(app) as client:
        assert client.post("/api/auth/session").status_code == 401
        for _ in range(2):
            response = client.post("/api/auth/session", headers={"X-API-Key": "guess"})
            assert response.status_code == 401
            assert "set-cookie" not in response.headers
        assert client.post("/api/auth/session", headers=ADMIN).status_code == 429


def test_an_explicit_wrong_key_is_rejected_even_with_a_session():
    from main import app

    with TestClient(app) as client:
        client.post("/api/auth/session", headers=ADMIN)
        assert client.get("/api/personal/watchlist", headers={"X-API-Key": "guess"}).status_code == 401


def test_session_tokens_expire_and_cannot_be_forged(monkeypatch):
    token, expires_at = security.issue_admin_session(now=1_000_000)
    assert expires_at == 1_000_000 + 30 * 86400
    assert security.admin_session_is_valid(token, now=expires_at - 1)
    assert not security.admin_session_is_valid(token, now=expires_at)

    version, _, signature = token.split(".")
    assert not security.admin_session_is_valid(f"{version}.{expires_at + 86400}.{signature}", now=1_000_000)
    assert not security.admin_session_is_valid("", now=1_000_000)
    assert not security.admin_session_is_valid("a.b.c.d", now=1_000_000)

    # Rotating the admin key ends every session.
    monkeypatch.setattr(security.settings, "ADMIN_API_KEY", "rotated-secret")
    assert not security.admin_session_is_valid(token, now=1_000_000)


def test_session_cookie_is_secure_in_production(monkeypatch):
    from fastapi import Response

    monkeypatch.setattr(security.settings, "ENVIRONMENT", "production")
    response = Response()
    security.set_admin_session_cookie(response)
    assert "Secure" in response.headers["set-cookie"]


def test_optional_admin_endpoints_accept_the_session(monkeypatch):
    from api import routers
    from main import app

    seen = []

    async def fake_decision_support(ticker, db, include_saved_scenarios):
        seen.append(include_saved_scenarios)
        return {"ticker": ticker}

    monkeypatch.setattr(routers, "get_decision_support", fake_decision_support)
    with TestClient(app) as client:
        client.get("/api/stocks/AAPL.US/decision-support")
        client.post("/api/auth/session", headers=ADMIN)
        client.get("/api/stocks/AAPL.US/decision-support")
        assert client.get("/api/stocks/AAPL.US/decision-support", headers={"X-API-Key": "guess"}).status_code == 401
    assert seen == [False, True]
