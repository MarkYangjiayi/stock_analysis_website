from typing import Any, Dict

import numpy as np
import pandas as pd


def _forward_returns(prices: pd.DataFrame, factor_dates: pd.DataFrame, horizon_days: int) -> pd.DataFrame:
    price_data = prices.copy()
    price_data["date"] = pd.to_datetime(price_data["date"])
    adjusted = pd.to_numeric(price_data["adjusted_close"], errors="coerce") if "adjusted_close" in price_data else pd.Series(index=price_data.index, dtype=float)
    price_data["price"] = adjusted.fillna(pd.to_numeric(price_data["close"], errors="coerce"))
    rows = []
    for ticker, observations in factor_dates.groupby("ticker"):
        series = price_data[price_data["ticker"] == ticker].dropna(subset=["price"]).sort_values("date")
        dates = list(series["date"])
        values = list(series["price"])
        for _, observation in observations.iterrows():
            as_of = pd.Timestamp(observation["as_of_date"])
            entry_index = next((index for index, price_date in enumerate(dates) if price_date > as_of), None)
            if entry_index is None or entry_index + horizon_days >= len(values):
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
    forward = _forward_returns(prices, factors[["ticker", "as_of_date"]], horizon_days)
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
