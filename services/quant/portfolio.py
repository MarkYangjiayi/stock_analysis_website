from dataclasses import dataclass
from typing import Dict, Optional

import pandas as pd


@dataclass(frozen=True)
class PortfolioConstructionResult:
    weights: Dict[str, float]
    cash_weight: float
    diagnostics: dict


def construct_long_only_weights(
    signals: pd.DataFrame,
    top_n: int = 30,
    max_position_weight: float = 0.05,
    max_sector_weight: float = 0.30,
    sector_column: str = "sector",
) -> PortfolioConstructionResult:
    if signals.empty:
        return PortfolioConstructionResult({}, 1.0, {"reason": "no eligible signals"})
    required = {"ticker", "score"}
    if not required.issubset(signals.columns):
        raise ValueError(f"signals must contain {sorted(required)}")
    candidates = (
        signals.dropna(subset=["ticker", "score"])
        .sort_values("score", ascending=False)
        .drop_duplicates("ticker", keep="first")
        .head(top_n)
        .copy()
    )
    if sector_column not in candidates:
        candidates[sector_column] = "Unknown"
    candidates[sector_column] = candidates[sector_column].fillna("Unknown")
    weights = {ticker: 0.0 for ticker in candidates["ticker"]}
    sectors = dict(zip(candidates["ticker"], candidates[sector_column]))
    sector_weights: Dict[str, float] = {}
    remaining = 1.0

    for _ in range(len(weights) * 4 + 1):
        active = [
            ticker for ticker in weights
            if weights[ticker] < max_position_weight - 1e-12
            and sector_weights.get(sectors[ticker], 0.0) < max_sector_weight - 1e-12
        ]
        if not active or remaining <= 1e-12:
            break
        equal_increment = remaining / len(active)
        allocated = 0.0
        for ticker in active:
            sector = sectors[ticker]
            increment = min(
                equal_increment,
                max_position_weight - weights[ticker],
                max_sector_weight - sector_weights.get(sector, 0.0),
                remaining - allocated,
            )
            if increment <= 0:
                continue
            weights[ticker] += increment
            sector_weights[sector] = sector_weights.get(sector, 0.0) + increment
            allocated += increment
        if allocated <= 1e-12:
            break
        remaining -= allocated

    weights = {ticker: weight for ticker, weight in weights.items() if weight > 1e-12}
    return PortfolioConstructionResult(
        weights=weights,
        cash_weight=max(0.0, 1.0 - sum(weights.values())),
        diagnostics={
            "selected": len(weights),
            "sector_weights": sector_weights,
            "max_position_weight": max_position_weight,
            "max_sector_weight": max_sector_weight,
            "uninvested_due_to_constraints": max(0.0, remaining),
        },
    )


def portfolio_turnover(old_weights: Dict[str, float], new_weights: Dict[str, float]) -> float:
    tickers = set(old_weights) | set(new_weights)
    stock_turnover = sum(abs(new_weights.get(ticker, 0.0) - old_weights.get(ticker, 0.0)) for ticker in tickers)
    old_cash = 1.0 - sum(old_weights.values())
    new_cash = 1.0 - sum(new_weights.values())
    return 0.5 * (stock_turnover + abs(new_cash - old_cash))
