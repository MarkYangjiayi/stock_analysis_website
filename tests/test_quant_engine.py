from datetime import date

import numpy as np
import pandas as pd

from services.quant.factor_engine import _price_factors, compute_factor_frame


def synthetic_snapshots(as_of: date) -> pd.DataFrame:
    rows = []
    for index in range(10):
        rows.append({
            "ticker": f"T{index}.US",
            "date": as_of,
            "sector": "Tech" if index < 5 else "Health",
            "pe_ratio": 10 + index,
            "pb_ratio": 1 + index / 10,
            "roe": 0.10 + index / 100,
            "gross_margin": 0.30 + index / 100,
            "debt_to_equity": 1.0 - index / 20,
            "sales_growth_5yr": 0.02 + index / 100,
        })
    return pd.DataFrame(rows)


def synthetic_prices(as_of: date, include_future_jump: bool) -> pd.DataFrame:
    dates = pd.bdate_range(end=as_of, periods=260)
    rows = []
    for ticker_index in range(10):
        for day_index, price_date in enumerate(dates):
            price = 100 + day_index * (0.05 + ticker_index * 0.005)
            rows.append({"ticker": f"T{ticker_index}.US", "date": price_date, "close": price, "adjusted_close": price})
        if include_future_jump:
            rows.append({
                "ticker": f"T{ticker_index}.US",
                "date": pd.Timestamp(as_of) + pd.Timedelta(days=3),
                "close": 10000 + ticker_index,
                "adjusted_close": 10000 + ticker_index,
            })
    return pd.DataFrame(rows)


def test_factor_engine_rejects_future_price_information():
    as_of = date(2025, 12, 31)
    snapshots = synthetic_snapshots(as_of)
    clean = compute_factor_frame(snapshots, synthetic_prices(as_of, False), as_of).set_index("ticker")
    future = compute_factor_frame(snapshots, synthetic_prices(as_of, True), as_of).set_index("ticker")
    pd.testing.assert_series_equal(clean["momentum"], future["momentum"])
    pd.testing.assert_series_equal(clean["composite"], future["composite"])


def test_price_factors_ignore_non_positive_prices():
    dates = pd.bdate_range(end="2025-12-31", periods=252)
    prices = pd.DataFrame({
        "ticker": ["ZERO.US"] * len(dates),
        "date": dates,
        "close": [0.0] + [100.0] * (len(dates) - 1),
        "adjusted_close": [0.0] + [100.0] * (len(dates) - 1),
    })

    result = _price_factors(prices, date(2025, 12, 31)).iloc[0]

    assert np.isfinite(result["momentum"])
    assert np.isfinite(result["low_volatility"])
