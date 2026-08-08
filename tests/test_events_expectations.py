from datetime import date

import pytest

from services.events_expectations import get_events_expectations
from services.raw_store import persist_snapshot


@pytest.mark.asyncio
async def test_events_and_expectations_extract_upcoming_and_reported_evidence(db_session):
    await persist_snapshot(
        db_session,
        "EODHD",
        "fundamentals",
        {
            "Earnings": {
                "History": {
                    "2025-12-31": {
                        "date": "2025-12-31",
                        "reportDate": "2026-01-20",
                        "epsActual": 1.20,
                        "epsEstimate": 1.10,
                        "epsDifference": 0.10,
                        "surprisePercent": 9.09,
                        "beforeAfterMarket": "AfterMarket",
                    },
                    "2026-03-31": {
                        "date": "2026-03-31",
                        "reportDate": "2026-04-21",
                        "epsActual": None,
                        "epsEstimate": 1.35,
                        "beforeAfterMarket": "BeforeMarket",
                    },
                },
                "Trend": {
                    "2026-06-30": {
                        "date": "2026-06-30",
                        "period": "+1q",
                        "epsTrendCurrent": "1.50",
                        "epsTrend30daysAgo": "1.45",
                        "earningsEstimateLow": "1.35",
                        "earningsEstimateHigh": "1.65",
                        "earningsEstimateGrowth": "0.125",
                        "revenueEstimateAvg": "1000000000",
                        "revenueEstimateLow": "950000000",
                        "revenueEstimateHigh": "1050000000",
                        "revenueEstimateGrowth": "0.10",
                        "earningsEstimateNumberOfAnalysts": "12",
                        "revenueEstimateNumberOfAnalysts": "10",
                        "epsRevisionsUpLast30days": "4",
                        "epsRevisionsDownLast30days": "1",
                    },
                },
            },
            "Highlights": {
                "WallStreetTargetPrice": "140.5",
                "DividendYield": "0.02",
                "DividendShare": "1.20",
            },
            "SplitsDividends": {
                "ExDividendDate": "2026-02-05",
                "DividendDate": "2026-02-20",
            },
        },
        details={"ticker": "TEST.US"},
    )

    result = await get_events_expectations(
        "TEST.US",
        db_session,
        reference_date=date(2026, 1, 1),
    )

    assert result["available"] is True
    assert result["next_event"]["kind"] == "dividend"
    assert result["next_event"]["event_date"] == date(2026, 2, 5)
    assert result["upcoming_events"][1]["kind"] == "earnings"
    assert result["recent_earnings"][0]["eps_surprise_percent"] == pytest.approx(9.09)
    assert result["expectations"][0]["eps_average"] == pytest.approx(1.5)
    assert result["expectations"][0]["eps_revisions_up_30d"] == 4
    assert result["wall_street_target_price"] == pytest.approx(140.5)


@pytest.mark.asyncio
async def test_stale_unreported_history_is_not_presented_as_an_event(db_session):
    await persist_snapshot(
        db_session,
        "EODHD",
        "fundamentals",
        {
            "Earnings": {
                "History": {
                    "2025-09-30": {
                        "date": "2025-09-30",
                        "reportDate": "2025-10-20",
                        "epsActual": None,
                    },
                },
                "Trend": {},
            },
        },
        details={"ticker": "STALE.US"},
    )

    result = await get_events_expectations(
        "STALE.US",
        db_session,
        reference_date=date(2026, 1, 1),
    )

    assert result["available"] is False
    assert result["upcoming_events"] == []
    assert result["recent_earnings"] == []
    assert result["data_quality_notes"] == [
        "No upcoming event date was published by the provider.",
        "No forward consensus estimates were published by the provider.",
    ]


@pytest.mark.asyncio
async def test_missing_snapshot_returns_non_blocking_empty_state(db_session):
    result = await get_events_expectations("MISSING.US", db_session, reference_date=date(2026, 1, 1))

    assert result["available"] is False
    assert result["next_event"] is None
    assert result["data_quality_notes"] == ["No fundamentals snapshot is available yet."]
