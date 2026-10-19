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
