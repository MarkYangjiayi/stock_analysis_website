from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from api.schemas import TreasuryYieldCurveResponse
from services.yield_curve import (
    YieldCurveUnavailable,
    build_yield_curve_payload,
    get_yield_curve,
    parse_treasury_csv,
)


def _observation(day: str, short: float, two: float, ten: float, thirty: float) -> dict:
    return {
        "date": day,
        "yields": {
            "1m": short,
            "6w": short,
            "2m": short,
            "3m": short,
            "4m": short,
            "6m": short,
            "1y": two,
            "2y": two,
            "3y": two,
            "5y": ten,
            "7y": ten,
            "10y": ten,
            "20y": thirty,
            "30y": thirty,
        },
    }


def _payload(period: str = "1y") -> dict:
    observations = [
        _observation("2025-09-11", 4.10, 4.00, 4.30, 4.55),
        _observation("2026-06-12", 3.80, 4.20, 4.60, 4.90),
        _observation("2026-08-12", 3.90, 4.40, 4.80, 5.10),
        _observation("2026-09-11", 4.07, 4.63, 4.96, 5.35),
    ]
    return build_yield_curve_payload(
        observations,
        period,  # type: ignore[arg-type]
        now=datetime(2026, 9, 14, 12, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 9, 14, 12, tzinfo=timezone.utc),
        provider_name="EODHD",
    )


def test_parse_treasury_csv_normalizes_maturities_and_missing_values():
    rows = parse_treasury_csv(
        "Date,1 Mo,1.5 Month,2 Mo,3 Mo,4 Mo,6 Mo,1 Yr,2 Yr,3 Yr,5 Yr,7 Yr,10 Yr,20 Yr,30 Yr\n"
        "09/11/2026,3.93,3.99,4.05,4.07,4.15,4.12,4.35,4.63,4.69,4.78,4.87,4.96,N/A,5.35\n"
        "not-a-date,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n"
    )

    assert len(rows) == 1
    assert rows[0]["date"] == "2026-09-11"
    assert rows[0]["yields"]["6w"] == 3.99
    assert rows[0]["yields"]["20y"] is None


def test_build_payload_includes_curve_comparisons_and_history():
    payload = _payload()
    validated = TypeAdapter(TreasuryYieldCurveResponse).validate_python(payload)

    assert validated.meta.as_of_date.isoformat() == "2026-09-11"
    assert validated.meta.provider_name == "EODHD"
    assert validated.meta.stale is False
    assert validated.latest.yields["10y"] == 4.96
    assert [snapshot.key for snapshot in validated.snapshots] == ["latest", "1m", "3m", "1y"]
    assert [snapshot.date.isoformat() for snapshot in validated.snapshots] == [
        "2026-09-11",
        "2026-08-12",
        "2026-06-12",
        "2025-09-11",
    ]


@pytest.mark.asyncio
async def test_get_yield_curve_falls_back_to_stale_disk_cache(monkeypatch):
    cache = {
        "schema_version": 1,
        "fetched_at": "2026-09-01T12:00:00+00:00",
        "provider_name": "EODHD",
        "observations": _payload()["observations"],
    }

    async def fail_refresh(*args, **kwargs):
        request = httpx.Request("GET", "https://example.test/yields")
        raise httpx.ConnectError("offline", request=request)

    monkeypatch.setattr("services.yield_curve._read_cache", lambda: cache)
    monkeypatch.setattr("services.yield_curve.fetch_treasury_observations", fail_refresh)

    payload = await get_yield_curve(
        "1y",
        now=datetime(2026, 9, 14, 12, tzinfo=timezone.utc),
    )

    assert payload["meta"]["stale"] is True
    assert "last cached observations" in payload["meta"]["warnings"][-1]


def test_yield_curve_route_and_unavailable_state(monkeypatch):
    from api import routers
    from main import app

    async def available(period):
        return _payload(period)

    monkeypatch.setattr(routers, "get_yield_curve", available)
    with TestClient(app) as client:
        response = client.get("/api/v1/yield-curve", params={"period": "3y"})
        assert response.status_code == 200
        assert response.json()["meta"]["period"] == "3y"

        invalid = client.get("/api/v1/yield-curve", params={"period": "10y"})
        assert invalid.status_code == 422

    async def unavailable(period):
        raise YieldCurveUnavailable("Treasury is offline")

    monkeypatch.setattr(routers, "get_yield_curve", unavailable)
    with TestClient(app) as client:
        response = client.get("/api/v1/yield-curve")
        assert response.status_code == 503
        assert response.json()["detail"] == "Treasury is offline"
