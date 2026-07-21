import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    BacktestRun,
    DailyPrice,
    FactorValue,
    SignalSnapshot,
    StrategyDefinition,
    UniverseMembership,
)
from services.quant.factor_engine import FACTOR_VERSION
from services.quant.portfolio import construct_long_only_weights, portfolio_turnover
from services.quant.risk import calculate_performance_metrics
from core.trading_calendar import us_market_close_utc
from core.time_utils import utc_now


@dataclass(frozen=True)
class BacktestConfig:
    start_date: date
    end_date: date
    factor_name: str = "composite"
    factor_version: str = FACTOR_VERSION
    universe: str = "SP500_RUSSELL2000"
    benchmark: str = "SPY.US"
    rebalance_frequency: str = "monthly"
    signal_lag_days: int = 1
    top_n: int = 30
    max_position_weight: float = 0.05
    max_sector_weight: float = 0.30
    transaction_cost_bps: float = 5.0
    slippage_bps: float = 5.0
    require_point_in_time_universe: bool = True
    missing_price_policy: str = "fail"

    def __post_init__(self):
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        if self.rebalance_frequency not in {"weekly", "monthly", "all"}:
            raise ValueError("rebalance_frequency must be weekly, monthly, or all")
        if self.missing_price_policy not in {"fail", "liquidate_last"}:
            raise ValueError("missing_price_policy must be fail or liquidate_last")
        if self.signal_lag_days < 1:
            raise ValueError("signal_lag_days must be at least 1")
        if self.top_n < 1:
            raise ValueError("top_n must be positive")
        if not 0 < self.max_position_weight <= 1:
            raise ValueError("max_position_weight must be in (0, 1]")
        if not 0 < self.max_sector_weight <= 1:
            raise ValueError("max_sector_weight must be in (0, 1]")
        if self.transaction_cost_bps < 0 or self.slippage_bps < 0:
            raise ValueError("cost assumptions cannot be negative")

    def json_dict(self) -> dict:
        values = asdict(self)
        values["start_date"] = self.start_date.isoformat()
        values["end_date"] = self.end_date.isoformat()
        return values


def _rebalance_dates(signal_dates: List[pd.Timestamp], frequency: str) -> List[pd.Timestamp]:
    unique = pd.DatetimeIndex(sorted(set(signal_dates)))
    if frequency == "all":
        return list(unique)
    if unique.empty:
        return []
    periods = unique.to_period("W" if frequency == "weekly" else "M")
    return list(pd.Series(unique, index=periods).groupby(level=0).max())


def _eligible_members(memberships: pd.DataFrame, universe: str, as_of: pd.Timestamp) -> Optional[set]:
    if memberships.empty:
        return None
    data = memberships[memberships["universe"] == universe].copy()
    data["effective_from"] = pd.to_datetime(data["effective_from"])
    data["effective_to"] = pd.to_datetime(data["effective_to"])
    mask = (data["effective_from"] <= as_of) & (data["effective_to"].isna() | (data["effective_to"] >= as_of))
    return set(data.loc[mask, "ticker"])


def run_backtest_from_frames(
    factors: pd.DataFrame,
    prices: pd.DataFrame,
    memberships: pd.DataFrame,
    config: BacktestConfig,
) -> Dict[str, Any]:
    if factors.empty:
        raise ValueError("No factor observations available for the requested period")
    factor_data = factors.copy()
    factor_data["as_of_date"] = pd.to_datetime(factor_data["as_of_date"])
    factor_data = factor_data[
        (factor_data["as_of_date"].dt.date >= config.start_date)
        & (factor_data["as_of_date"].dt.date <= config.end_date)
    ]
    if "score" not in factor_data:
        factor_data["score"] = pd.to_numeric(factor_data["normalized_value"], errors="coerce")
    if "sector" not in factor_data:
        factor_data["sector"] = "Unknown"

    price_data = prices.copy()
    price_data["date"] = pd.to_datetime(price_data["date"])
    adjusted = pd.to_numeric(price_data.get("adjusted_close"), errors="coerce") if "adjusted_close" in price_data else pd.Series(index=price_data.index, dtype=float)
    raw_close = pd.to_numeric(price_data.get("close"), errors="coerce")
    price_data["price"] = adjusted.fillna(raw_close)
    price_data = price_data[
        (price_data["date"].dt.date >= config.start_date)
        & (price_data["date"].dt.date <= config.end_date)
    ]
    pivot = price_data.pivot_table(index="date", columns="ticker", values="price", aggfunc="last").sort_index()
    if config.benchmark not in pivot:
        raise ValueError(f"Benchmark {config.benchmark} has no price history")
    trading_dates = list(pivot[config.benchmark].dropna().index)
    if len(trading_dates) < 2:
        raise ValueError("Insufficient benchmark trading dates")
    asset_returns = pivot.pct_change(fill_method=None).replace([float("inf"), float("-inf")], pd.NA).fillna(0.0)

    signal_dates = _rebalance_dates(list(factor_data["as_of_date"]), config.rebalance_frequency)
    execution_schedule: Dict[pd.Timestamp, pd.Timestamp] = {}
    for signal_date in signal_dates:
        future_dates = [trading_date for trading_date in trading_dates if trading_date > signal_date]
        if len(future_dates) >= config.signal_lag_days:
            execution_schedule[future_dates[config.signal_lag_days - 1]] = signal_date

    current_weights: Dict[str, float] = {}
    current_sectors: Dict[str, str] = {}
    daily_returns = []
    benchmark_returns = []
    equity_curve = []
    rebalances = []
    sector_contribution: Dict[str, float] = {}
    equity = 1.0
    total_turnover = 0.0
    total_cost = 0.0
    missing_membership_dates = []
    missing_price_events = []
    cost_rate = (config.transaction_cost_bps + config.slippage_bps) / 10000.0

    for trading_date in trading_dates:
        if trading_date.date() < config.start_date or trading_date.date() > config.end_date:
            continue
        returns_row = asset_returns.loc[trading_date]
        day_return = 0.0
        liquidations = []
        for ticker, weight in current_weights.items():
            raw_price = pivot.loc[trading_date].get(ticker)
            if pd.isna(raw_price):
                event = {"ticker": ticker, "date": trading_date.date().isoformat()}
                missing_price_events.append(event)
                if config.missing_price_policy == "fail":
                    raise ValueError(
                        f"Missing held-security price for {ticker} on {trading_date.date()}; "
                        "load a delisting return or use missing_price_policy='liquidate_last'"
                    )
                liquidations.append(ticker)
                continue
            asset_return = float(returns_row.get(ticker, 0.0) or 0.0)
            contribution = weight * asset_return
            day_return += contribution
            sector = current_sectors.get(ticker, "Unknown")
            sector_contribution[sector] = sector_contribution.get(sector, 0.0) + contribution
        for ticker in liquidations:
            current_weights.pop(ticker, None)
            current_sectors.pop(ticker, None)

        rebalance_cost = 0.0
        if trading_date in execution_schedule:
            signal_date = execution_schedule[trading_date]
            signals = factor_data[factor_data["as_of_date"] == signal_date].copy()
            if "available_at" in signals:
                available = pd.to_datetime(signals["available_at"])
                signals = signals[available <= us_market_close_utc(trading_date.date())]
            eligible = _eligible_members(memberships, config.universe, signal_date)
            if eligible is None:
                missing_membership_dates.append(signal_date.date().isoformat())
                if config.require_point_in_time_universe:
                    raise ValueError(f"Missing point-in-time universe membership for {signal_date.date()}")
            else:
                signals = signals[signals["ticker"].isin(eligible)]
            if signals.empty:
                rebalances.append({
                    "signal_date": signal_date.date().isoformat(),
                    "execution_date": trading_date.date().isoformat(),
                    "status": "skipped",
                    "reason": "no eligible signal was available by the execution close",
                    "turnover": 0.0,
                    "cost": 0.0,
                    "positions": len(current_weights),
                    "cash_weight": max(0.0, 1.0 - sum(current_weights.values())),
                    "weights": dict(current_weights),
                    "_signals": [],
                })
                equity *= 1.0 + day_return
                daily_returns.append((trading_date, day_return))
                benchmark_return = float(returns_row.get(config.benchmark, 0.0) or 0.0)
                benchmark_returns.append((trading_date, benchmark_return))
                equity_curve.append({
                    "date": trading_date.date().isoformat(),
                    "equity": equity,
                    "daily_return": day_return,
                })
                continue
            construction = construct_long_only_weights(
                signals[["ticker", "score", "sector"]],
                top_n=config.top_n,
                max_position_weight=config.max_position_weight,
                max_sector_weight=config.max_sector_weight,
            )
            new_weights = construction.weights
            turnover = portfolio_turnover(current_weights, new_weights)
            rebalance_cost = turnover * cost_rate
            total_turnover += turnover
            total_cost += rebalance_cost
            current_weights = new_weights
            current_sectors = dict(zip(signals["ticker"], signals["sector"].fillna("Unknown")))
            ranked = (
                signals.dropna(subset=["ticker", "score"])
                .sort_values(["score", "ticker"], ascending=[False, True])
                .drop_duplicates("ticker")
            )
            selected_signals = [
                {
                    "ticker": row.ticker,
                    "score": float(row.score),
                    "rank": rank,
                    "target_weight": new_weights.get(row.ticker),
                }
                for rank, row in enumerate(ranked.itertuples(index=False), start=1)
                if row.ticker in new_weights
            ]
            rebalances.append({
                "signal_date": signal_date.date().isoformat(),
                "execution_date": trading_date.date().isoformat(),
                "turnover": turnover,
                "cost": rebalance_cost,
                "positions": len(new_weights),
                "cash_weight": construction.cash_weight,
                "weights": new_weights,
                "_signals": selected_signals,
            })

        day_return -= rebalance_cost
        equity *= 1.0 + day_return
        daily_returns.append((trading_date, day_return))
        benchmark_return = float(returns_row.get(config.benchmark, 0.0) or 0.0)
        benchmark_returns.append((trading_date, benchmark_return))
        equity_curve.append({"date": trading_date.date().isoformat(), "equity": equity, "daily_return": day_return})

    portfolio_series = pd.Series({index: value for index, value in daily_returns}).sort_index()
    benchmark_series = pd.Series({index: value for index, value in benchmark_returns}).sort_index()
    metrics = calculate_performance_metrics(
        portfolio_series,
        benchmark_series,
        total_turnover=total_turnover,
        total_cost=total_cost,
    )
    return {
        "metrics": metrics,
        "equity_curve": equity_curve,
        "attribution": {"sector_return_contribution": sector_contribution},
        "rebalances": rebalances,
        "diagnostics": {
            "factor_version": config.factor_version,
            "signal_lag_days": config.signal_lag_days,
            "missing_membership_dates": sorted(set(missing_membership_dates)),
            "missing_price_events": missing_price_events,
            "lookahead_guard": "signals execute on a later trading close and earn returns from the following close",
        },
    }


async def run_and_store_backtest(db: AsyncSession, config: BacktestConfig, name: str = "Low Frequency Multi-Factor") -> BacktestRun:
    serialized_config = json.dumps(config.json_dict(), sort_keys=True, separators=(",", ":"))
    config_hash = hashlib.sha256(serialized_config.encode("utf-8")).hexdigest()[:12]
    strategy_version = f"{config.factor_version}-{config_hash}"
    strategy_result = await db.execute(
        select(StrategyDefinition).where(
            StrategyDefinition.name == name,
            StrategyDefinition.version == strategy_version,
        )
    )
    strategy = strategy_result.scalar_one_or_none()
    if strategy is None:
        strategy = StrategyDefinition(name=name, version=strategy_version, config=config.json_dict())
        db.add(strategy)
        await db.flush()
    run = BacktestRun(
        strategy_id=strategy.id,
        name=name,
        start_date=config.start_date,
        end_date=config.end_date,
        status="running",
        config=config.json_dict(),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    try:
        factor_result = await db.execute(
            select(FactorValue).where(
                FactorValue.factor_name == config.factor_name,
                FactorValue.version == config.factor_version,
                FactorValue.as_of_date >= config.start_date,
                FactorValue.as_of_date <= config.end_date,
            )
        )
        factor_rows = factor_result.scalars().all()
        factors = pd.DataFrame([
            {
                "ticker": row.ticker,
                "as_of_date": row.as_of_date,
                "normalized_value": row.normalized_value,
                "available_at": row.available_at,
                "sector": (row.details or {}).get("sector", "Unknown"),
            }
            for row in factor_rows
        ])
        symbols = sorted(set(factors.get("ticker", [])) | {config.benchmark})
        price_rows = []
        for start in range(0, len(symbols), 500):
            result = await db.execute(
                select(DailyPrice).where(
                    DailyPrice.ticker.in_(symbols[start:start + 500]),
                    DailyPrice.date >= config.start_date,
                    DailyPrice.date <= config.end_date,
                )
            )
            price_rows.extend(result.scalars().all())
        prices = pd.DataFrame([
            {
                "ticker": row.ticker,
                "date": row.date,
                "close": float(row.close) if row.close is not None else None,
                "adjusted_close": float(row.adjusted_close) if row.adjusted_close is not None else None,
            }
            for row in price_rows
        ])
        membership_result = await db.execute(
            select(UniverseMembership).where(
                UniverseMembership.universe == config.universe,
                UniverseMembership.effective_from <= config.end_date,
                (UniverseMembership.effective_to.is_(None) | (UniverseMembership.effective_to >= config.start_date)),
            )
        )
        memberships = pd.DataFrame([
            {
                "universe": row.universe,
                "ticker": row.ticker,
                "effective_from": row.effective_from,
                "effective_to": row.effective_to,
            }
            for row in membership_result.scalars().all()
        ])
        result = run_backtest_from_frames(factors, prices, memberships, config)
        signal_dates = [date.fromisoformat(item["signal_date"]) for item in result["rebalances"]]
        if signal_dates:
            await db.execute(
                delete(SignalSnapshot).where(
                    SignalSnapshot.strategy_id == strategy.id,
                    SignalSnapshot.as_of_date.in_(signal_dates),
                )
            )
            for rebalance in result["rebalances"]:
                as_of_date = date.fromisoformat(rebalance["signal_date"])
                for signal in rebalance["_signals"]:
                    db.add(
                        SignalSnapshot(
                            strategy_id=strategy.id,
                            ticker=signal["ticker"],
                            as_of_date=as_of_date,
                            score=signal["score"],
                            rank=signal["rank"],
                            target_weight=signal["target_weight"],
                            factor_details={
                                "factor_name": config.factor_name,
                                "factor_version": config.factor_version,
                                "execution_date": rebalance["execution_date"],
                            },
                        )
                    )
        diagnostic_rebalances = [
            {key: value for key, value in rebalance.items() if key != "_signals"}
            for rebalance in result["rebalances"]
        ]
        run.status = "completed"
        run.metrics = result["metrics"]
        run.equity_curve = result["equity_curve"]
        run.attribution = result["attribution"]
        run.diagnostics = {**result["diagnostics"], "rebalances": diagnostic_rebalances}
        run.finished_at = utc_now()
        await db.commit()
        await db.refresh(run)
        return run
    except Exception as exc:
        run.status = "failed"
        run.diagnostics = {"error": str(exc)}
        run.finished_at = utc_now()
        await db.commit()
        raise
