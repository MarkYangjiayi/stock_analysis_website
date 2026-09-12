from datetime import date

import pytest

from models import DailyPrice, Ticker
from services.analyzer import get_analyzed_stock_data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("interval", "expected_dates", "expected_volumes", "expected_closes"),
    [
        (
            "1d",
            ["1987-01-29", "1987-01-30", "1987-02-02", "1987-02-06", "1987-02-09"],
            [1200, 300, 400, 500, 600],
            [0.5, 0.49, 90, 100, 100],
        ),
        ("1wk", ["1987-01-30", "1987-02-06", "1987-02-13"], [1500, 900, 600], [0.49, 100, 100]),
        ("1mo", ["1987-01-31", "1987-02-28"], [1500, 1500], [0.49, 100]),
    ],
)
async def test_history_preserves_provider_volume_across_price_adjustments(
    db_session, interval, expected_dates, expected_volumes, expected_closes
):
    db_session.add(Ticker(ticker="VOLUME.US", name="Historical Volume"))
    # Cover a large historical split factor, a dividend adjustment, an
    # unadjusted session and the fallback when adjusted close is absent.
    for day, close, adjusted_close, volume in [
        ("1987-01-29", 224, 0.5, 1200),
        ("1987-01-30", 224, 0.49, 300),
        ("1987-02-02", 100, 90, 400),
        ("1987-02-06", 100, 100, 500),
        ("1987-02-09", 100, None, 600),
    ]:
        db_session.add(DailyPrice(
            ticker="VOLUME.US", date=date.fromisoformat(day),
            open=close, high=close, low=close, close=close,
            adjusted_close=adjusted_close, volume=volume,
        ))
    await db_session.commit()

    result = await get_analyzed_stock_data("VOLUME.US", db_session, interval=interval)

    history = result["historical_data"]
    assert [point["date"] for point in history] == expected_dates
    assert [point["volume"] for point in history] == pytest.approx(expected_volumes)
    assert [point["close"] for point in history] == pytest.approx(expected_closes)
