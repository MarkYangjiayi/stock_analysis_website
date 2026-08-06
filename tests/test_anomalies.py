import asyncio
import os
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from starlette.requests import Request

from core.config import settings
from core.security import _client_identifier
from models import AnomalyScanRun, StockScreenerSnapshot
from services.ai_assistant import AttributionGenerationError
from services.anomaly_detector import (
    AnomalyDataUnavailable,
    AnomalyScanData,
    scan_and_analyze_anomalies,
)
from services.anomaly_scans import execute_anomaly_scan
from services.news_fetcher import fetch_yahoo_news


def test_alembic_upgrade_adds_anomaly_scan_runs(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "anomaly-migration.db"
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{database_path}",
        "ENVIRONMENT": "test",
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=project_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(anomaly_scan_runs)"
            )
        }
    assert columns == {
        "id",
        "trigger",
        "status",
        "active_key",
        "owner_token",
        "lease_expires_at",
        "requested_limit",
        "threshold_pct",
        "universe_as_of",
        "quote_as_of",
        "results",
        "error_message",
        "created_at",
        "started_at",
        "finished_at",
    }


def _quote(ticker: str, change: float, timestamp: datetime) -> dict:
    return {
        "code": ticker,
        "change_p": change,
        "timestamp": int(timestamp.timestamp()),
    }


def _news(title: str = "Catalyst") -> dict:
    return {
        "title": title,
        "link": "https://finance.yahoo.com/example",
        "pub_date": datetime.now(timezone.utc).isoformat(),
        "summary": "A source-backed catalyst.",
        "publisher": "Example News",
    }


async def _seed_universe(db_session, tickers: list[str]) -> date:
    universe_date = date(2025, 1, 2)
    for index, ticker in enumerate(tickers):
        db_session.add(StockScreenerSnapshot(
            ticker=ticker,
            date=universe_date,
            name=f"Company {ticker}",
            market_cap=Decimal(1_000_000 - index),
        ))
    await db_session.commit()
    return universe_date


@pytest.mark.asyncio
async def test_anomaly_scan_uses_quote_time_and_configured_threshold(
    db_session,
    monkeypatch,
):
    from services import anomaly_detector

    universe_date = await _seed_universe(
        db_session,
        ["AAA.US", "BBB.US", "CCC.US"],
    )
    quote_time = datetime.now(timezone.utc)

    async def fake_quotes(*args, **kwargs):
        return [
            _quote("AAA", 8.5, quote_time),
            _quote("BBB.US", -6.0, quote_time),
            _quote("CCC", 3.9, quote_time),
        ]

    async def fake_news(*args, **kwargs):
        return [_news()]

    async def fake_attribution(*args, **kwargs):
        return "Source-backed explanation [1]"

    monkeypatch.setattr(anomaly_detector, "get_bulk_realtime_prices", fake_quotes)
    monkeypatch.setattr(anomaly_detector, "fetch_yahoo_news", fake_news)
    monkeypatch.setattr(
        anomaly_detector,
        "generate_anomaly_attribution",
        fake_attribution,
    )

    scan = await scan_and_analyze_anomalies(
        db_session,
        limit_count=5,
        threshold_pct=4.0,
    )

    assert scan.universe_as_of == universe_date
    assert scan.quote_as_of == quote_time.replace(microsecond=0)
    assert [item["ticker"] for item in scan.results] == ["AAA.US", "BBB.US"]
    assert all(item["date"] != universe_date.isoformat() for item in scan.results)
    assert scan.results[0]["quote_timestamp"].endswith("Z")
    assert scan.results[0]["news"][0]["title"] == "Catalyst"
    assert scan.results[0]["attribution_status"] == "completed"


@pytest.mark.asyncio
async def test_anomaly_scan_uses_top_1000_market_cap_universe(
    db_session,
    monkeypatch,
):
    from services import anomaly_detector

    tickers = [f"T{index:04d}.US" for index in range(1_001)]
    await _seed_universe(db_session, tickers)
    quote_time = datetime.now(timezone.utc).replace(microsecond=0)

    async def fake_quotes(*args, **kwargs):
        return [
            _quote(tickers[999], -8.0, quote_time),
            _quote(tickers[1_000], -12.0, quote_time),
        ]

    async def fake_news(*args, **kwargs):
        return []

    monkeypatch.setattr(anomaly_detector, "get_bulk_realtime_prices", fake_quotes)
    monkeypatch.setattr(anomaly_detector, "fetch_yahoo_news", fake_news)

    scan = await scan_and_analyze_anomalies(db_session)

    assert [item["ticker"] for item in scan.results] == [tickers[999]]


@pytest.mark.asyncio
async def test_anomaly_scan_excludes_prior_session_symbols_from_fresh_batch(
    db_session,
    monkeypatch,
):
    from services import anomaly_detector

    await _seed_universe(db_session, ["FRESH.US", "STALE.US"])
    fresh_time = datetime.now(timezone.utc).replace(microsecond=0)
    stale_time = fresh_time - timedelta(days=1)

    async def fake_quotes(*args, **kwargs):
        return [
            _quote("FRESH", 6.0, fresh_time),
            _quote("STALE", 25.0, stale_time),
        ]

    async def fake_news(*args, **kwargs):
        return [_news()]

    async def fake_attribution(*args, **kwargs):
        return "Source-backed explanation [1]"

    monkeypatch.setattr(
        anomaly_detector,
        "latest_completed_us_session",
        lambda reference_date: stale_time.astimezone(
            anomaly_detector._NEW_YORK
        ).date(),
    )
    monkeypatch.setattr(anomaly_detector, "get_bulk_realtime_prices", fake_quotes)
    monkeypatch.setattr(anomaly_detector, "fetch_yahoo_news", fake_news)
    monkeypatch.setattr(
        anomaly_detector,
        "generate_anomaly_attribution",
        fake_attribution,
    )

    scan = await scan_and_analyze_anomalies(db_session, limit_count=1)

    assert scan.quote_as_of == fresh_time
    assert [item["ticker"] for item in scan.results] == ["FRESH.US"]


@pytest.mark.asyncio
async def test_scheduled_scan_rejects_entirely_prior_session_batch(
    db_session,
    monkeypatch,
):
    from services import anomaly_detector

    await _seed_universe(db_session, ["STALE.US"])
    stale_time = (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).replace(microsecond=0)

    async def fake_quotes(*args, **kwargs):
        return [_quote("STALE", 12.0, stale_time)]

    monkeypatch.setattr(
        anomaly_detector,
        "latest_completed_us_session",
        lambda reference_date: stale_time.astimezone(
            anomaly_detector._NEW_YORK
        ).date(),
    )
    monkeypatch.setattr(anomaly_detector, "get_bulk_realtime_prices", fake_quotes)

    with pytest.raises(AnomalyDataUnavailable, match="current session"):
        await scan_and_analyze_anomalies(
            db_session,
            require_current_session=True,
        )


@pytest.mark.asyncio
async def test_anomaly_scan_requires_finite_price_changes(
    db_session,
    monkeypatch,
):
    from services import anomaly_detector

    await _seed_universe(
        db_session,
        ["NONE.US", "NAN.US", "INFINITY.US"],
    )
    quote_time = datetime.now(timezone.utc)

    async def fake_quotes(*args, **kwargs):
        return [
            {"code": "NONE", "change_p": None, "timestamp": quote_time.timestamp()},
            {"code": "NAN", "change_p": "NaN", "timestamp": quote_time.timestamp()},
            {
                "code": "INFINITY",
                "change_p": "Infinity",
                "timestamp": quote_time.timestamp(),
            },
        ]

    monkeypatch.setattr(anomaly_detector, "get_bulk_realtime_prices", fake_quotes)

    with pytest.raises(AnomalyDataUnavailable, match="usable price changes"):
        await scan_and_analyze_anomalies(db_session)


@pytest.mark.asyncio
async def test_anomaly_attribution_is_bounded_and_partial_failures_survive(
    db_session,
    monkeypatch,
):
    from services import anomaly_detector

    await _seed_universe(
        db_session,
        ["AAA.US", "BBB.US", "CCC.US", "DDD.US"],
    )
    quote_time = datetime.now(timezone.utc)
    active = 0
    max_active = 0

    async def fake_quotes(*args, **kwargs):
        return [
            _quote("AAA", 9.0, quote_time),
            _quote("BBB", 8.0, quote_time),
            _quote("CCC", 7.0, quote_time),
            _quote("DDD", 6.0, quote_time),
        ]

    async def fake_news(ticker, **kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return [_news(ticker)]

    async def fake_attribution(ticker, **kwargs):
        if ticker == "BBB.US":
            raise AttributionGenerationError("provider failed")
        return f"{ticker} explanation [1]"

    monkeypatch.setattr(settings, "ANOMALY_ATTRIBUTION_CONCURRENCY", 2)
    monkeypatch.setattr(anomaly_detector, "get_bulk_realtime_prices", fake_quotes)
    monkeypatch.setattr(anomaly_detector, "fetch_yahoo_news", fake_news)
    monkeypatch.setattr(
        anomaly_detector,
        "generate_anomaly_attribution",
        fake_attribution,
    )

    scan = await scan_and_analyze_anomalies(db_session, limit_count=4)

    assert max_active == 2
    assert len(scan.results) == 4
    failed = next(item for item in scan.results if item["ticker"] == "BBB.US")
    assert failed["attribution_status"] == "attribution_unavailable"
    assert failed["news"][0]["title"] == "BBB.US"


@pytest.mark.asyncio
async def test_anomaly_attribution_deadline_includes_semaphore_wait(
    db_session,
    monkeypatch,
):
    from services import anomaly_detector

    await _seed_universe(db_session, ["AAA.US", "BBB.US"])
    quote_time = datetime.now(timezone.utc)
    entered_news_fetch = 0

    async def fake_quotes(*args, **kwargs):
        return [
            _quote("AAA", 9.0, quote_time),
            _quote("BBB", 8.0, quote_time),
        ]

    async def blocking_news(*args, **kwargs):
        nonlocal entered_news_fetch
        entered_news_fetch += 1
        await asyncio.sleep(1)
        return [_news()]

    monkeypatch.setattr(settings, "ANOMALY_ATTRIBUTION_CONCURRENCY", 1)
    monkeypatch.setattr(settings, "ANOMALY_ATTRIBUTION_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(anomaly_detector, "get_bulk_realtime_prices", fake_quotes)
    monkeypatch.setattr(anomaly_detector, "fetch_yahoo_news", blocking_news)

    scan = await scan_and_analyze_anomalies(db_session, limit_count=2)

    assert entered_news_fetch == 1
    assert [item["attribution_status"] for item in scan.results] == [
        "timed_out",
        "timed_out",
    ]


@pytest.mark.asyncio
async def test_anomaly_scan_propagates_market_data_failure(
    db_session,
    monkeypatch,
):
    from services import anomaly_detector

    await _seed_universe(db_session, ["AAA.US"])

    async def fake_quotes(*args, **kwargs):
        return None

    monkeypatch.setattr(anomaly_detector, "get_bulk_realtime_prices", fake_quotes)

    with pytest.raises(AnomalyDataUnavailable, match="market data"):
        await scan_and_analyze_anomalies(db_session)


@pytest.mark.asyncio
async def test_recovery_only_expires_dead_process_leases(
    db_session,
    monkeypatch,
):
    from services import anomaly_scans
    from services.anomaly_scans import recover_interrupted_anomaly_scans

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    active_runs = [
        AnomalyScanRun(
            trigger="manual",
            status="running",
            active_key="market",
            owner_token="live-other-process",
            lease_expires_at=now + timedelta(minutes=1),
            requested_limit=5,
            threshold_pct=4.0,
            results=[],
        ),
        AnomalyScanRun(
            trigger="post_market_summary",
            status="running",
            active_key="expired",
            owner_token="dead-process",
            lease_expires_at=now - timedelta(seconds=1),
            requested_limit=10,
            threshold_pct=4.0,
            results=[],
        ),
    ]
    completed = AnomalyScanRun(
        trigger="manual",
        status="completed",
        requested_limit=5,
        threshold_pct=4.0,
        results=[],
        finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add_all([*active_runs, completed])
    await db_session.commit()

    async def should_not_run(*args, **kwargs):
        pytest.fail("A live lease owned by another process was claimed")

    monkeypatch.setattr(
        anomaly_scans,
        "scan_and_analyze_anomalies",
        should_not_run,
    )
    await recover_interrupted_anomaly_scans()
    await execute_anomaly_scan(active_runs[0].id)
    db_session.expire_all()
    runs = (
        await db_session.execute(
            select(AnomalyScanRun).order_by(AnomalyScanRun.id)
        )
    ).scalars().all()

    assert [run.status for run in runs] == [
        "running",
        "failed",
        "completed",
    ]
    assert runs[0].active_key == "market"
    assert runs[0].owner_token == "live-other-process"
    assert runs[0].finished_at is None
    assert runs[1].active_key is None
    assert runs[1].owner_token is None
    assert runs[1].lease_expires_at is None
    assert runs[1].finished_at is not None
    assert (
        runs[1].error_message
        == "Scan ownership lease expired before completion"
    )
    assert runs[2].error_message is None


@pytest.mark.asyncio
async def test_durable_scan_records_completed_payload(db_session, monkeypatch):
    from services import anomaly_scans

    run = AnomalyScanRun(
        trigger="manual",
        status="queued",
        active_key="market",
        requested_limit=5,
        threshold_pct=4.0,
        results=[],
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    quote_time = datetime.now(timezone.utc).replace(microsecond=0)

    async def fake_scan(*args, **kwargs):
        return AnomalyScanData(
            universe_as_of=date(2026, 7, 30),
            quote_as_of=quote_time,
            results=[{
                "ticker": "AAA.US",
                "company_name": "AAA",
                "date": "2026-07-30",
                "quote_timestamp": quote_time.isoformat().replace("+00:00", "Z"),
                "price_change": 8.0,
                "ai_analysis": "Explanation",
                "attribution_status": "completed",
                "news": [],
                "top_news_links": [],
            }],
        )

    monkeypatch.setattr(
        anomaly_scans,
        "scan_and_analyze_anomalies",
        fake_scan,
    )

    await execute_anomaly_scan(run.id)
    await db_session.refresh(run)

    assert run.status == "completed"
    assert run.active_key is None
    assert run.owner_token is None
    assert run.lease_expires_at is None
    assert run.universe_as_of == date(2026, 7, 30)
    assert run.quote_as_of == quote_time.replace(tzinfo=None)
    assert run.results[0]["ticker"] == "AAA.US"
    assert run.error_message is None


@pytest.mark.asyncio
async def test_scheduled_scan_waits_for_incompatible_active_limit(
    db_session,
    monkeypatch,
):
    from database import async_session_maker
    from services import anomaly_scans

    active = AnomalyScanRun(
        trigger="morning_briefing",
        status="running",
        active_key="market",
        owner_token="other-process",
        lease_expires_at=(
            datetime.now(timezone.utc) + timedelta(minutes=1)
        ).replace(tzinfo=None),
        requested_limit=5,
        threshold_pct=4.0,
        results=[],
    )
    db_session.add(active)
    await db_session.commit()
    await db_session.refresh(active)

    quote_time = datetime.now(timezone.utc).replace(microsecond=0)

    async def complete_active():
        await asyncio.sleep(0.05)
        async with async_session_maker() as session:
            run = await session.get(AnomalyScanRun, active.id)
            run.status = "completed"
            run.active_key = None
            run.owner_token = None
            run.lease_expires_at = None
            run.results = [{"ticker": f"OLD{index}.US"} for index in range(5)]
            run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await session.commit()

    async def fake_scan(*args, limit_count, **kwargs):
        assert limit_count == 10
        assert kwargs["require_current_session"] is True
        return AnomalyScanData(
            universe_as_of=date(2026, 7, 30),
            quote_as_of=quote_time,
            results=[
                {"ticker": f"NEW{index}.US"}
                for index in range(limit_count)
            ],
        )

    monkeypatch.setattr(
        anomaly_scans,
        "scan_and_analyze_anomalies",
        fake_scan,
    )

    release_task = asyncio.create_task(complete_active())
    results = await anomaly_scans.run_persisted_anomaly_scan(
        trigger="post_market_summary",
        limit_count=10,
    )
    await release_task

    db_session.expire_all()
    runs = (
        await db_session.execute(
            select(AnomalyScanRun).order_by(AnomalyScanRun.id)
        )
    ).scalars().all()
    assert len(runs) == 2
    assert runs[1].trigger == "post_market_summary"
    assert runs[1].requested_limit == 10
    assert runs[1].status == "completed"
    assert len(results) == 10
    assert results[0]["ticker"] == "NEW0.US"


def test_scheduled_scan_does_not_reuse_manual_freshness_policy():
    from services.anomaly_scans import _scan_satisfies_request

    manual_scan = AnomalyScanRun(
        trigger="manual",
        status="running",
        requested_limit=10,
        threshold_pct=4.0,
        results=[],
    )

    assert not _scan_satisfies_request(
        manual_scan,
        trigger="morning_briefing",
        limit_count=5,
        threshold_pct=4.0,
    )


@pytest.mark.asyncio
async def test_scheduled_scan_retries_after_compatible_active_failure(
    db_session,
    monkeypatch,
):
    from database import async_session_maker
    from services import anomaly_scans

    active = AnomalyScanRun(
        trigger="morning_briefing",
        status="running",
        active_key="market",
        owner_token="other-process",
        lease_expires_at=(
            datetime.now(timezone.utc) + timedelta(minutes=1)
        ).replace(tzinfo=None),
        requested_limit=5,
        threshold_pct=4.0,
        results=[],
    )
    db_session.add(active)
    await db_session.commit()
    await db_session.refresh(active)

    quote_time = datetime.now(timezone.utc).replace(microsecond=0)

    async def fail_active():
        await asyncio.sleep(0.05)
        async with async_session_maker() as session:
            run = await session.get(AnomalyScanRun, active.id)
            run.status = "failed"
            run.active_key = None
            run.owner_token = None
            run.lease_expires_at = None
            run.error_message = "Provider failed"
            run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await session.commit()

    async def fake_scan(*args, limit_count, **kwargs):
        assert kwargs["require_current_session"] is True
        return AnomalyScanData(
            universe_as_of=date(2026, 7, 30),
            quote_as_of=quote_time,
            results=[
                {"ticker": f"RETRY{index}.US"}
                for index in range(limit_count)
            ],
        )

    monkeypatch.setattr(
        anomaly_scans,
        "scan_and_analyze_anomalies",
        fake_scan,
    )

    failure_task = asyncio.create_task(fail_active())
    results = await anomaly_scans.run_persisted_anomaly_scan(
        trigger="morning_briefing",
        limit_count=5,
    )
    await failure_task

    db_session.expire_all()
    runs = (
        await db_session.execute(
            select(AnomalyScanRun).order_by(AnomalyScanRun.id)
        )
    ).scalars().all()
    assert [run.status for run in runs] == ["failed", "completed"]
    assert len(results) == 5
    assert results[0]["ticker"] == "RETRY0.US"


@pytest.mark.asyncio
async def test_anomaly_api_queues_single_flight_and_reads_latest(
    db_session,
    monkeypatch,
):
    from api import routers
    from core import security
    from main import app

    scheduled = []
    security._expensive_limiter._requests.clear()
    monkeypatch.setattr(routers, "schedule_anomaly_scan", scheduled.append)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        first = await client.post("/api/market/anomalies/scans")
        second = await client.post("/api/market/anomalies/scans")
        status_response = await client.get(
            f"/api/market/anomalies/scans/{first.json()['id']}"
        )
        latest_before = await client.get("/api/market/anomalies")

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert scheduled == [first.json()["id"]]
    assert status_response.json()["status"] == "queued"
    assert latest_before.json() is None

    result = await db_session.execute(
        select(AnomalyScanRun).where(AnomalyScanRun.id == first.json()["id"])
    )
    run = result.scalar_one()
    run.status = "completed"
    run.results = []
    run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        latest_after = await client.get("/api/market/anomalies")

    assert latest_after.status_code == 200
    assert latest_after.json()["id"] == run.id
    assert latest_after.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_anomaly_api_background_task_reaches_completed(
    monkeypatch,
):
    from core import security
    from main import app
    from services import anomaly_scans

    quote_time = datetime.now(timezone.utc).replace(microsecond=0)
    scan_started = asyncio.Event()
    release_scan = asyncio.Event()

    async def fake_scan(*args, **kwargs):
        scan_started.set()
        await release_scan.wait()
        return AnomalyScanData(
            universe_as_of=date(2026, 7, 30),
            quote_as_of=quote_time,
            results=[],
        )

    security._expensive_limiter._requests.clear()
    monkeypatch.setattr(
        anomaly_scans,
        "scan_and_analyze_anomalies",
        fake_scan,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        started = await asyncio.wait_for(
            client.post("/api/market/anomalies/scans"),
            timeout=0.5,
        )
        scan_id = started.json()["id"]
        await asyncio.wait_for(scan_started.wait(), timeout=0.5)
        running = await client.get(
            f"/api/market/anomalies/scans/{scan_id}"
        )
        assert running.json()["status"] == "running"
        release_scan.set()
        terminal = None
        for _ in range(20):
            response = await client.get(
                f"/api/market/anomalies/scans/{scan_id}"
            )
            terminal = response.json()
            if terminal["status"] == "completed":
                break
            await asyncio.sleep(0.01)

    assert started.status_code == 202
    assert terminal["status"] == "completed"
    assert terminal["quote_as_of"] == quote_time.isoformat().replace(
        "+00:00",
        "Z",
    )


def test_proxy_client_identifier_only_trusts_configured_peers(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "172.16.0.0/12")
    trusted_request = Request({
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [(
            b"x-forwarded-for",
            b"198.51.100.99, 203.0.113.9, 172.18.0.1",
        )],
        "client": ("172.18.0.1", 1234),
    })
    untrusted_request = Request({
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [(b"x-forwarded-for", b"203.0.113.10")],
        "client": ("198.51.100.7", 1234),
    })

    assert _client_identifier(trusted_request) == "203.0.113.9"
    assert _client_identifier(untrusted_request) == "198.51.100.7"


@pytest.mark.asyncio
async def test_news_lookup_preserves_dotted_us_symbols_and_skips_undated():
    observed = {}
    current = time.gmtime()
    feed = SimpleNamespace(
        bozo=False,
        entries=[
            SimpleNamespace(
                title="Current item",
                link="https://finance.yahoo.com/current",
                published_parsed=current,
                summary="<p>Summary</p>",
                publisher="Publisher",
            ),
            SimpleNamespace(
                title="Undated item",
                link="https://finance.yahoo.com/undated",
                published_parsed=None,
                summary="Summary",
                publisher="Publisher",
            ),
        ],
    )

    class FakeResponse:
        content = b"feed"

        def raise_for_status(self):
            return None

    class FakeClient:
        async def get(self, url, **kwargs):
            observed["url"] = url
            observed["params"] = kwargs["params"]
            return FakeResponse()

    from services import news_fetcher

    original_parse = news_fetcher.feedparser.parse
    news_fetcher.feedparser.parse = lambda content: feed
    try:
        results = await fetch_yahoo_news(
            "BRK.B.US",
            client=FakeClient(),
        )
    finally:
        news_fetcher.feedparser.parse = original_parse

    assert observed["params"]["s"] == "BRK.B"
    assert [item["title"] for item in results] == ["Current item"]
