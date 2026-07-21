from typing import Dict, Optional

import numpy as np
import pandas as pd


def calculate_performance_metrics(
    returns: pd.Series,
    benchmark_returns: Optional[pd.Series] = None,
    annualization: int = 252,
    risk_free_rate: float = 0.0,
    total_turnover: float = 0.0,
    total_cost: float = 0.0,
) -> Dict[str, float]:
    values = pd.to_numeric(returns, errors="coerce").fillna(0.0)
    if values.empty:
        raise ValueError("Cannot calculate risk metrics for an empty return series")
    equity = (1.0 + values).cumprod()
    total_return = float(equity.iloc[-1] - 1.0)
    years = max(len(values) / annualization, 1.0 / annualization)
    annual_return = float((1.0 + total_return) ** (1.0 / years) - 1.0) if total_return > -1 else -1.0
    annual_volatility = float(values.std(ddof=0) * np.sqrt(annualization))
    sharpe = (annual_return - risk_free_rate) / annual_volatility if annual_volatility > 0 else 0.0
    # Include the initial capital (1.0) as a peak so a loss on the first
    # observation is not incorrectly reported as zero drawdown.
    running_peak = equity.cummax().clip(lower=1.0)
    drawdown = equity / running_peak - 1.0
    daily_target = risk_free_rate / annualization
    downside = np.minimum(values - daily_target, 0.0)
    downside_volatility = float(np.sqrt(np.mean(np.square(downside))) * np.sqrt(annualization))
    sortino = (annual_return - risk_free_rate) / downside_volatility if downside_volatility > 0 else 0.0
    var_95 = float(values.quantile(0.05))
    tail = values[values <= var_95]
    cvar_95 = float(tail.mean()) if not tail.empty else var_95
    metrics = {
        "total_return": total_return,
        "annualized_return": annual_return,
        "annualized_volatility": annual_volatility,
        "sharpe_ratio": float(sharpe),
        "sortino_ratio": float(sortino),
        "max_drawdown": float(drawdown.min()),
        "var_95_daily": var_95,
        "cvar_95_daily": cvar_95,
        "total_turnover": float(total_turnover),
        "total_cost": float(total_cost),
    }
    if benchmark_returns is not None:
        benchmark = pd.to_numeric(benchmark_returns, errors="coerce").reindex(values.index).fillna(0.0)
        active = values - benchmark
        benchmark_variance = float(benchmark.var(ddof=0))
        covariance = float(((values - values.mean()) * (benchmark - benchmark.mean())).mean())
        beta = covariance / benchmark_variance if benchmark_variance > 0 else 0.0
        benchmark_annual = float((1.0 + benchmark).prod() ** (1.0 / years) - 1.0)
        tracking_error = float(active.std(ddof=0) * np.sqrt(annualization))
        information_ratio = float(active.mean() * annualization / tracking_error) if tracking_error > 0 else 0.0
        metrics.update({
            "benchmark_total_return": float((1.0 + benchmark).prod() - 1.0),
            "beta": beta,
            "alpha_annualized": float(annual_return - (risk_free_rate + beta * (benchmark_annual - risk_free_rate))),
            "tracking_error": tracking_error,
            "information_ratio": information_ratio,
        })
    return metrics
