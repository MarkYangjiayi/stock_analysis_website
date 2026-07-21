from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from services.quant.backtest import BacktestConfig, run_backtest_from_frames
from services.quant.factor_engine import _price_factors, compute_factor_frame
from services.quant.portfolio import construct_long_only_weights, portfolio_turnover
from services.quant.research import _forward_returns, evaluate_factor
from services.quant.risk import calculate_performance_metrics


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


def test_portfolio_respects_position_and_sector_caps():
    signals = pd.DataFrame([
        {"ticker": f"A{i}", "score": 10 - i, "sector": "Tech" if i < 5 else "Health"}
        for i in range(10)
    ])
    result = construct_long_only_weights(signals, top_n=10, max_position_weight=0.15, max_sector_weight=0.55)
    assert max(result.weights.values()) <= 0.15 + 1e-9
    assert max(result.diagnostics["sector_weights"].values()) <= 0.55 + 1e-9
    assert sum(result.weights.values()) <= 1.0 + 1e-9
    assert portfolio_turnover({}, result.weights) == pytest.approx(sum(result.weights.values()))


def backtest_frames():
    dates = pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"])
    price_map = {
        "AAA.US": [100, 200, 220, 220],
        "BBB.US": [100, 100, 100, 100],
        "SPY.US": [100, 100, 100, 100],
    }
    prices = pd.DataFrame([
        {"ticker": ticker, "date": price_date, "close": values[index], "adjusted_close": values[index]}
        for ticker, values in price_map.items()
        for index, price_date in enumerate(dates)
    ])
    factors = pd.DataFrame([
        {"ticker": "AAA.US", "as_of_date": pd.Timestamp("2025-01-02"), "normalized_value": 2.0, "available_at": datetime(2025, 1, 2, 23, 59), "sector": "Tech"},
        {"ticker": "BBB.US", "as_of_date": pd.Timestamp("2025-01-02"), "normalized_value": 1.0, "available_at": datetime(2025, 1, 2, 23, 59), "sector": "Health"},
    ])
    memberships = pd.DataFrame([
        {"universe": "TEST", "ticker": ticker, "effective_from": date(2024, 1, 1), "effective_to": None}
        for ticker in ["AAA.US", "BBB.US"]
    ])
    return factors, prices, memberships


def test_backtest_applies_signal_lag_and_transaction_costs():
    factors, prices, memberships = backtest_frames()
    config = BacktestConfig(
        start_date=date(2025, 1, 2),
        end_date=date(2025, 1, 7),
        universe="TEST",
        rebalance_frequency="all",
        top_n=2,
        max_position_weight=1.0,
        max_sector_weight=1.0,
        transaction_cost_bps=0,
        slippage_bps=0,
    )
    result = run_backtest_from_frames(factors, prices, memberships, config)
    curve = {row["date"]: row["daily_return"] for row in result["equity_curve"]}
    assert curve["2025-01-03"] == 0.0  # The 100% jump happened before execution at this close.
    assert curve["2025-01-06"] == pytest.approx(0.05)  # 50% AAA weight earns half of its 10% return.
    assert result["diagnostics"]["signal_lag_days"] == 1


def test_backtest_rejects_signals_published_after_execution_close():
    factors, prices, memberships = backtest_frames()
    factors["available_at"] = datetime(2025, 1, 3, 22, 0)  # XNYS closed at 21:00 UTC.
    config = BacktestConfig(
        start_date=date(2025, 1, 2),
        end_date=date(2025, 1, 7),
        universe="TEST",
        rebalance_frequency="all",
        top_n=2,
        max_position_weight=1.0,
        max_sector_weight=1.0,
        transaction_cost_bps=0,
        slippage_bps=0,
    )
    result = run_backtest_from_frames(factors, prices, memberships, config)
    assert result["rebalances"][0]["positions"] == 0
    assert result["metrics"]["total_return"] == 0


def test_unavailable_rebalance_keeps_the_existing_portfolio():
    factors, prices, memberships = backtest_frames()
    late = factors.copy()
    late["as_of_date"] = pd.Timestamp("2025-01-03")
    late["available_at"] = datetime(2025, 1, 6, 22, 0)  # After the Jan 6 XNYS close.
    factors = pd.concat([factors, late], ignore_index=True)
    config = BacktestConfig(
        start_date=date(2025, 1, 2),
        end_date=date(2025, 1, 7),
        universe="TEST",
        rebalance_frequency="all",
        top_n=2,
        max_position_weight=1.0,
        max_sector_weight=1.0,
        transaction_cost_bps=0,
        slippage_bps=0,
    )
    result = run_backtest_from_frames(factors, prices, memberships, config)
    assert result["rebalances"][1]["status"] == "skipped"
    assert result["rebalances"][1]["positions"] == 2
    assert result["rebalances"][1]["weights"] == result["rebalances"][0]["weights"]


def test_monthly_backtest_seeds_from_latest_pre_start_signal():
    factors = pd.DataFrame([
        {
            "ticker": ticker,
            "as_of_date": signal_date,
            "normalized_value": score,
            "available_at": signal_date.to_pydatetime(),
            "sector": sector,
        }
        for signal_date, scores in [
            (pd.Timestamp("2024-12-31"), [("AAA.US", 2.0, "Tech"), ("BBB.US", 1.0, "Health")]),
            (pd.Timestamp("2025-01-02"), [("AAA.US", 2.0, "Tech"), ("BBB.US", 1.0, "Health")]),
            (pd.Timestamp("2025-01-31"), [("AAA.US", 1.0, "Tech"), ("BBB.US", 2.0, "Health")]),
        ]
        for ticker, score, sector in scores
    ])
    trading_dates = pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-31", "2025-02-03"])
    prices = pd.DataFrame([
        {
            "ticker": ticker,
            "date": trading_date,
            "close": 100 + day_index,
            "adjusted_close": 100 + day_index,
        }
        for ticker in ["AAA.US", "BBB.US", "SPY.US"]
        for day_index, trading_date in enumerate(trading_dates)
    ])
    memberships = pd.DataFrame([
        {"universe": "TEST", "ticker": ticker, "effective_from": date(2024, 1, 1), "effective_to": None}
        for ticker in ["AAA.US", "BBB.US"]
    ])
    config = BacktestConfig(
        start_date=date(2025, 1, 2),
        end_date=date(2025, 2, 3),
        universe="TEST",
        rebalance_frequency="monthly",
        top_n=2,
        max_position_weight=1.0,
        max_sector_weight=1.0,
        transaction_cost_bps=0,
        slippage_bps=0,
    )

    result = run_backtest_from_frames(factors, prices, memberships, config)

    assert [row["signal_date"] for row in result["rebalances"]] == ["2024-12-31", "2025-01-31"]
    assert result["rebalances"][0]["execution_date"] == "2025-01-02"
    assert result["diagnostics"]["pre_start_signal_date"] == "2024-12-31"


def test_backtest_refuses_missing_point_in_time_universe():
    factors, prices, _ = backtest_frames()
    config = BacktestConfig(
        start_date=date(2025, 1, 2), end_date=date(2025, 1, 7), universe="TEST", rebalance_frequency="all"
    )
    with pytest.raises(ValueError, match="Missing point-in-time universe"):
        run_backtest_from_frames(factors, prices, pd.DataFrame(), config)


def test_backtest_rejects_unknown_execution_policies():
    with pytest.raises(ValueError, match="rebalance_frequency"):
        BacktestConfig(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 2, 1),
            rebalance_frequency="sometimes",
        )
    with pytest.raises(ValueError, match="missing_price_policy"):
        BacktestConfig(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 2, 1),
            missing_price_policy="ignore",
        )


def test_factor_research_reports_positive_rank_ic():
    factor_dates = pd.to_datetime(["2025-01-02", "2025-02-03"])
    factors = pd.DataFrame([
        {"ticker": f"T{i}", "as_of_date": factor_date, "normalized_value": float(i)}
        for factor_date in factor_dates
        for i in range(10)
    ])
    trading_dates = pd.bdate_range("2025-01-02", periods=70)
    prices = pd.DataFrame([
        {
            "ticker": f"T{i}",
            "date": trading_date,
            "close": 100 * ((1 + i * 0.0005) ** day_index),
            "adjusted_close": 100 * ((1 + i * 0.0005) ** day_index),
        }
        for i in range(10)
        for day_index, trading_date in enumerate(trading_dates)
    ])
    result = evaluate_factor(factors, prices, horizon_days=5, quantiles=5)
    assert result["mean_rank_ic"] > 0.9
    assert result["long_short_spread"] > 0


def test_factor_research_uses_first_close_after_availability():
    prices = pd.DataFrame([
        {"ticker": "AAA.US", "date": trading_date, "close": price, "adjusted_close": price}
        for trading_date, price in [
            ("2025-01-03", 100.0),
            ("2025-01-06", 200.0),
            ("2025-01-07", 210.0),
            ("2025-01-08", 220.0),
        ]
    ])
    factors = pd.DataFrame([{
        "ticker": "AAA.US",
        "as_of_date": pd.Timestamp("2025-01-02"),
        "available_at": pd.Timestamp("2025-01-06 23:00:00"),
    }])

    result = _forward_returns(prices, factors, horizon_days=1)

    assert result.iloc[0]["forward_return"] == pytest.approx(220.0 / 210.0 - 1.0)


def test_factor_research_rejects_immature_forward_returns_cleanly():
    factors = pd.DataFrame([
        {
            "ticker": "AAA.US",
            "as_of_date": pd.Timestamp("2025-01-03"),
            "normalized_value": 1.0,
        }
    ])
    prices = pd.DataFrame([
        {
            "ticker": "AAA.US",
            "date": pd.Timestamp("2025-01-06"),
            "close": 100.0,
            "adjusted_close": 100.0,
        }
    ])

    with pytest.raises(ValueError, match="No aligned forward returns"):
        evaluate_factor(factors, prices, horizon_days=21)


def test_risk_metrics_include_benchmark_and_drawdown():
    returns = pd.Series([0.01, -0.02, 0.01, 0.005])
    benchmark = pd.Series([0.005, -0.01, 0.004, 0.002])
    metrics = calculate_performance_metrics(returns, benchmark)
    assert metrics["max_drawdown"] < 0
    assert "beta" in metrics
    assert "tracking_error" in metrics


def test_risk_metrics_count_a_first_day_loss_as_drawdown():
    metrics = calculate_performance_metrics(pd.Series([-0.10, 0.0]))
    assert metrics["max_drawdown"] == pytest.approx(-0.10)
