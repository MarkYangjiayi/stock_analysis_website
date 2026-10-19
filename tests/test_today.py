from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from database import async_session_maker
from models import DailyReportRun
from services import today

NOW = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)  # Monday 11:00 New York
REGIME = {"schema_version": 1, "items": [{"key": "trend", "label": "趋势", "state": "震荡", "tone": "grey",
                                          "detail": "SPY 距 200 日线 +5.7%", "as_of": "2026-10-16"}],
          "unavailable": ["利率曲线"]}


@pytest.fixture(autouse=True)
def empty_cache():
    today._cache.clear()
    yield
    today._cache.clear()


def _run(report_type, report_date, *, regime=None, movers=(), status="delivered", captured="T14:00:00+00:00"):
    context = {"report_date": report_date, "captured_at": f"{report_date}{captured}", "quotes": []}
    if regime:
        context["regime"] = regime
    context["anomaly_clusters"] = {"as_of": report_date, "movers": len(movers), "clusters": []}
    return DailyReportRun(report_type=report_type, renderer_version="cross-asset-v3.1", status=status,
                          market_context=context, source_results=list(movers), content="",
                          notification_delivered=status == "delivered")


@pytest.mark.asyncio
async def test_evidence_uses_the_newest_report_and_the_newest_regime(db_session):
    mover = {"ticker": "CLF.US", "company_name": "Cleveland-Cliffs Inc", "price_change": 11.92,
             "quote_timestamp": "2026-10-19T13:44:00Z",
             "news": [{"title": "Cleveland-Cliffs beats estimates", "link": "https://example.com/clf"}]}
    db_session.add_all([
        _run("morning_briefing", "2026-10-16", regime=REGIME),
        DailyReportRun(report_type="weekly_digest", renderer_version="weekly", status="delivered",
                       market_context={"week": "2026-10-16"}, source_results=[], content=""),
        _run("morning_briefing", "2026-10-19", movers=[mover], status="delivery_failed"),
    ])
    await db_session.commit()

    evidence = await today.latest_report_evidence()

    assert evidence["report_date"] == "2026-10-19" and evidence["status"] == "delivery_failed"
    assert evidence["delivered"] is False
    assert evidence["regime"] == REGIME and evidence["regime_captured_at"] == "2026-10-16T14:00:00+00:00"
    assert evidence["movers"] == [{
        "ticker": "CLF.US", "company_name": "Cleveland-Cliffs Inc", "price_change": 11.92,
        "quote_timestamp": "2026-10-19T13:44:00Z",
        "news_title": "Cleveland-Cliffs beats estimates", "news_link": "https://example.com/clf",
    }]


@pytest.mark.asyncio
async def test_evidence_is_none_before_any_daily_report():
    assert await today.latest_report_evidence() is None


def _fake_sources(monkeypatch, calls, *, events_error=None):
    async def price_signals(symbols, target, **kwargs):
        calls.setdefault("price", []).append((symbols, target))
        return {"oversold": 30.0, "overbought": 70.0, "near_extreme_pct": 1.0, "target": target.isoformat(),
                "items": {"ASTS.US": {"as_of": "2026-10-16", "close": 50.0, "rsi": 25.0,
                                      "high_52w": 90.0, "low_52w": 20.0}}}

    async def expectation_signals(symbols, reference_date):
        calls.setdefault("expectations", []).append(symbols)
        return {"items": {}}

    async def collect_events(assets, now, *, start=None, end=None):
        calls.setdefault("events", []).append(([asset.ticker for asset in assets], start, end))
        if events_error:
            raise events_error
        return {"from": start.isoformat(), "to": end.isoformat(), "economic_available": True,
                "earnings_available": True, "total": 1,
                "items": [{"kind": "earnings", "name": "ASTS.US", "date": "2026-10-21", "session": "AfterMarket"}]}

    monkeypatch.setattr(today, "price_signals", price_signals)
    monkeypatch.setattr(today, "expectation_signals", expectation_signals)
    monkeypatch.setattr(today, "collect_events", collect_events)
    monkeypatch.setattr(today.settings, "DAILY_REPORT_CORE_SYMBOLS", "AAPL.US,0700.HK")


@pytest.mark.asyncio
async def test_today_tags_the_whole_watchlist_and_reuses_the_cache(monkeypatch):
    calls = {}
    _fake_sources(monkeypatch, calls)

    first = await today.build_today(["ASTS.US", "UNH.US"], now=NOW)
    second = await today.build_today(["ASTS.US", "UNH.US"], now=NOW)

    assert first["report"] is None and first["unavailable"] == []
    assert first["watchlist"]["items"] == [
        {"ticker": "ASTS.US", "tags": [{"text": "RSI 25 超卖", "color": "blue"}], "as_of": "截至 2026-10-16"},
        {"ticker": "UNH.US", "tags": [], "as_of": ""},
    ]
    # Prices are read for the last completed session; events span seven calendar days.
    assert calls["price"] == [(["ASTS.US", "UNH.US"], date(2026, 10, 16))]
    assert calls["events"] == [(["AAPL.US", "0700.HK", "ASTS.US", "UNH.US"], date(2026, 10, 19), date(2026, 10, 25))]
    assert first["events"]["window_days"] == 7 and first["events"]["items"][0]["name"] == "ASTS.US"
    assert second["watchlist"] == first["watchlist"] and len(calls["price"]) == 1


@pytest.mark.asyncio
async def test_a_failed_section_is_named_and_retried_on_the_next_load(monkeypatch):
    calls = {}
    _fake_sources(monkeypatch, calls, events_error=RuntimeError("calendar down"))

    first = await today.build_today(["ASTS.US"], now=NOW)
    await today.build_today(["ASTS.US"], now=NOW)

    assert first["events"] is None and first["unavailable"] == ["未来 7 天日历"]
    assert first["watchlist"]["items"][0]["tags"]
    assert len(calls["events"]) == 2


def test_today_endpoint_requires_the_admin_key(monkeypatch):
    from main import app

    _fake_sources(monkeypatch, {})
    with TestClient(app) as client:
        assert client.get("/api/personal/today").status_code == 401
        assert client.get("/api/personal/today", headers={"X-API-Key": "wrong"}).status_code == 401
        client.put("/api/personal/watchlist", headers={"X-API-Key": "test-secret"}, json={"tickers": ["ASTS"]})
        response = client.get("/api/personal/today", headers={"X-API-Key": "test-secret"})
    assert response.status_code == 200
    body = response.json()
    assert body["report"] is None
    assert [item["ticker"] for item in body["watchlist"]["items"]] == ["ASTS.US"]
    assert body["events"]["items"][0]["kind"] == "earnings"
