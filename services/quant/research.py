from typing import Any, Dict

import numpy as np
import pandas as pd

from core.trading_calendar import is_us_market_session, us_market_close_utc


def _forward_returns(prices: pd.DataFrame, factor_dates: pd.DataFrame, horizon_days: int) -> pd.DataFrame:
    price_data = prices.copy()
    price_data["date"] = pd.to_datetime(price_data["date"])
    adjusted = pd.to_numeric(price_data["adjusted_close"], errors="coerce") if "adjusted_close" in price_data else pd.Series(index=price_data.index, dtype=float)
    price_data["price"] = adjusted.fillna(pd.to_numeric(price_data["close"], errors="coerce"))
    price_data = price_data.dropna(subset=["ticker", "date", "price"])
    price_data = price_data[
        price_data["date"].map(lambda value: is_us_market_session(value.date()))
    ]
    price_groups = {}
    for ticker, group in price_data.groupby("ticker"):
        series = group.sort_values("date").drop_duplicates("date", keep="last")
        dates = pd.DatetimeIndex(series["date"])
        close_times = pd.DatetimeIndex([us_market_close_utc(value.date()) for value in dates])
        price_groups[ticker] = (dates, close_times, series["price"].to_numpy())

    rows = []
    for ticker, observations in factor_dates.groupby("ticker"):
        price_group = price_groups.get(ticker)
        if price_group is None:
            continue
        dates, close_times, values = price_group
        for _, observation in observations.iterrows():
            as_of = pd.Timestamp(observation["as_of_date"])
            available_at = pd.Timestamp(observation.get("available_at", as_of))
            if pd.isna(available_at):
                available_at = as_of
            if available_at.tzinfo is not None:
                available_at = available_at.tz_convert("UTC").tz_localize(None)
            entry_index = max(
                int(dates.searchsorted(as_of, side="right")),
                int(close_times.searchsorted(available_at, side="right")),
            )
            if entry_index + horizon_days >= len(values):
                continue
            entry = values[entry_index]
            exit_value = values[entry_index + horizon_days]
            if entry and entry > 0:
                rows.append({
                    "ticker": ticker,
                    "as_of_date": as_of,
                    "forward_return": exit_value / entry - 1.0,
                })
    # Keep the merge keys even when no observation has reached the requested
    # forward horizon yet. This turns a normal "sample not mature" condition
    # into the ValueError handled by the API instead of an internal KeyError.
    return pd.DataFrame(rows, columns=["ticker", "as_of_date", "forward_return"])


def evaluate_factor(
    factor_values: pd.DataFrame,
    prices: pd.DataFrame,
    horizon_days: int = 21,
    quantiles: int = 5,
) -> Dict[str, Any]:
    if factor_values.empty:
        raise ValueError("factor_values cannot be empty")
    factors = factor_values.copy()
    factors["as_of_date"] = pd.to_datetime(factors["as_of_date"])
    if "score" not in factors:
        factors["score"] = pd.to_numeric(factors["normalized_value"], errors="coerce")
    forward_columns = ["ticker", "as_of_date"]
    if "available_at" in factors:
        forward_columns.append("available_at")
    forward = _forward_returns(prices, factors[forward_columns], horizon_days)
    merged = factors.merge(forward, on=["ticker", "as_of_date"], how="inner").dropna(subset=["score", "forward_return"])
    if merged.empty:
        raise ValueError("No aligned forward returns are available")

    daily_ic = pd.Series({
        as_of: group["score"].rank().corr(group["forward_return"].rank())
        for as_of, group in merged.groupby("as_of_date")
    }, dtype=float).dropna()
    quantile_rows = []
    top_sets = []
    for as_of, group in merged.groupby("as_of_date"):
        if group["score"].nunique() < 2:
            continue
        bins = min(quantiles, group["score"].nunique())
        ranked = group.copy()
        ranked["quantile"] = pd.qcut(ranked["score"].rank(method="first"), bins, labels=False) + 1
        for quantile, values in ranked.groupby("quantile"):
            quantile_rows.append({
                "as_of_date": as_of.date().isoformat(),
                "quantile": int(quantile),
                "mean_forward_return": float(values["forward_return"].mean()),
                "count": len(values),
            })
        top_sets.append((as_of, set(ranked.loc[ranked["quantile"] == bins, "ticker"])))

    aggregate = pd.DataFrame(quantile_rows)
    aggregate_returns = (
        aggregate.groupby("quantile")["mean_forward_return"].mean().to_dict() if not aggregate.empty else {}
    )
    turnovers = []
    for (_, previous), (_, current) in zip(top_sets, top_sets[1:]):
        denominator = max(len(previous), 1)
        turnovers.append(1.0 - len(previous & current) / denominator)
    quantile_keys = sorted(aggregate_returns)
    monotonicity = 0.0
    if len(quantile_keys) >= 2:
        monotonicity = float(
            pd.Series(quantile_keys).rank().corr(
                pd.Series([aggregate_returns[key] for key in quantile_keys]).rank()
            )
        )

    ic_std = float(daily_ic.std(ddof=0)) if not daily_ic.empty else 0.0
    return {
        "observations": len(merged),
        "dates": int(merged["as_of_date"].nunique()),
        "horizon_days": horizon_days,
        "mean_rank_ic": float(daily_ic.mean()) if not daily_ic.empty else 0.0,
        "ic_information_ratio": float(daily_ic.mean() / ic_std) if ic_std > 0 else 0.0,
        "positive_ic_rate": float((daily_ic > 0).mean()) if not daily_ic.empty else 0.0,
        "quantile_returns": {str(int(key)): float(value) for key, value in aggregate_returns.items()},
        "long_short_spread": float(aggregate_returns.get(max(quantile_keys), 0.0) - aggregate_returns.get(min(quantile_keys), 0.0)) if quantile_keys else 0.0,
        "monotonicity": monotonicity,
        "top_quantile_turnover": float(np.mean(turnovers)) if turnovers else 0.0,
        "daily_ic": [{"date": index.date().isoformat(), "rank_ic": float(value)} for index, value in daily_ic.items()],
    }
