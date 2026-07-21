from datetime import date
from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session_maker
from models import DailyPrice, DataPublication, FactorValue, StockScreenerSnapshot
from services.pipeline_runs import begin_pipeline_run, finish_pipeline_run, publish_dataset


FACTOR_VERSION = "lfq-v1"
FACTOR_NAMES = ("value", "quality", "growth", "momentum", "low_volatility", "composite")


def _safe_numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)


def _winsorized_zscore(values: pd.Series, sectors: pd.Series) -> pd.Series:
    series = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan)
    sector_medians = series.groupby(sectors).transform("median")
    series = series.fillna(sector_medians).fillna(series.median())
    if series.isna().all():
        return pd.Series(0.0, index=series.index)
    low, high = series.quantile([0.01, 0.99])
    clipped = series.clip(low, high)
    neutral = clipped - clipped.groupby(sectors).transform("mean")
    std = neutral.std(ddof=0)
    if not std or np.isnan(std):
        return pd.Series(0.0, index=series.index)
    return (neutral - neutral.mean()) / std


def _price_factors(prices: pd.DataFrame, as_of_date: date) -> pd.DataFrame:
    if prices.empty:
        return pd.DataFrame(columns=["ticker", "momentum", "low_volatility"])
    data = prices.copy()
    data["date"] = pd.to_datetime(data["date"])
    data = data[data["date"].dt.date <= as_of_date]
    adjusted = pd.to_numeric(data["adjusted_close"], errors="coerce") if "adjusted_close" in data else pd.Series(index=data.index, dtype=float)
    close = pd.to_numeric(data["close"], errors="coerce") if "close" in data else pd.Series(index=data.index, dtype=float)
    data["price"] = adjusted.fillna(close)
    rows = []
    for ticker, group in data.sort_values("date").groupby("ticker"):
        series = group["price"].dropna()
        momentum = np.nan
        if len(series) >= 252:
            momentum = series.iloc[-22] / series.iloc[-252] - 1.0
        elif len(series) >= 63:
            momentum = series.iloc[-1] / series.iloc[-63] - 1.0
        returns = (
            series.pct_change(fill_method=None)
            .replace([np.inf, -np.inf], np.nan)
            .dropna()
            .tail(126)
        )
        low_vol = -returns.std(ddof=0) * np.sqrt(252) if len(returns) >= 20 else np.nan
        rows.append({"ticker": ticker, "momentum": momentum, "low_volatility": low_vol})
    return pd.DataFrame(rows)


def compute_factor_frame(
    snapshots: pd.DataFrame,
    prices: pd.DataFrame,
    as_of_date: date,
) -> pd.DataFrame:
    """Compute point-in-time, sector-neutral cross-sectional factors.

    Callers must provide snapshots and prices no later than as_of_date. The
    function enforces that cutoff again to make look-ahead failures testable.
    """
    if snapshots.empty:
        return pd.DataFrame()
    frame = snapshots.copy()
    if "date" in frame:
        frame["date"] = pd.to_datetime(frame["date"])
        frame = frame[frame["date"].dt.date <= as_of_date]
        frame = frame.sort_values("date").drop_duplicates("ticker", keep="last")
    frame = frame.reset_index(drop=True)
    sectors = frame.get("sector", pd.Series("Unknown", index=frame.index)).fillna("Unknown")

    pe = _safe_numeric(frame, "pe_ratio")
    pb = _safe_numeric(frame, "pb_ratio")
    value_raw = pd.concat(
        [(1.0 / pe.where(pe > 0)).rename("earnings_yield"), (1.0 / pb.where(pb > 0)).rename("book_to_price")],
        axis=1,
    ).mean(axis=1, skipna=True)
    quality_raw = pd.concat(
        [
            _safe_numeric(frame, "roe").rename("roe"),
            _safe_numeric(frame, "gross_margin").rename("gross_margin"),
            (-_safe_numeric(frame, "debt_to_equity")).rename("leverage"),
        ],
        axis=1,
    ).mean(axis=1, skipna=True)
    growth_raw = _safe_numeric(frame, "sales_growth_5yr")

    price_factor_frame = _price_factors(prices, as_of_date)
    frame = frame.merge(price_factor_frame, on="ticker", how="left", suffixes=("", "_price"))
    sectors = frame.get("sector", pd.Series("Unknown", index=frame.index)).fillna("Unknown")
    raw_map = {
        "value": value_raw.reindex(frame.index),
        "quality": quality_raw.reindex(frame.index),
        "growth": growth_raw.reindex(frame.index),
        "momentum": _safe_numeric(frame, "momentum"),
        "low_volatility": _safe_numeric(frame, "low_volatility"),
    }
    normalized = {}
    for name, values in raw_map.items():
        frame[f"{name}_raw"] = values
        normalized[name] = _winsorized_zscore(values, sectors)
        frame[name] = normalized[name]
    frame["composite"] = pd.DataFrame(normalized).mean(axis=1)
    frame["composite_raw"] = frame["composite"]
    frame["as_of_date"] = as_of_date
    frame["version"] = FACTOR_VERSION
    return frame


async def _load_price_frame(db: AsyncSession, tickers: Iterable[str], as_of_date: date) -> pd.DataFrame:
    rows = []
    symbols = list(tickers)
    cutoff = pd.Timestamp(as_of_date) - pd.Timedelta(days=550)
    for start in range(0, len(symbols), 500):
        result = await db.execute(
            select(DailyPrice.ticker, DailyPrice.date, DailyPrice.close, DailyPrice.adjusted_close).where(
                DailyPrice.ticker.in_(symbols[start:start + 500]),
                DailyPrice.date <= as_of_date,
                DailyPrice.date >= cutoff.date(),
            )
        )
        rows.extend(result.all())
    return pd.DataFrame(rows, columns=["ticker", "date", "close", "adjusted_close"])


async def compute_and_store_factors(db: AsyncSession, as_of_date: date) -> dict:
    run_id = await begin_pipeline_run("factor_cross_section", as_of_date, FACTOR_VERSION)
    try:
        publication_result = await db.execute(
            select(DataPublication).where(
                DataPublication.dataset == "screener",
                DataPublication.as_of_date == as_of_date,
                DataPublication.status == "published",
            )
        )
        screener_publication = publication_result.scalar_one_or_none()
        if screener_publication is None:
            raise ValueError(
                f"Screener snapshot {as_of_date} is not a quality-gated publication; factors were not computed"
            )
        snapshot_result = await db.execute(
            select(StockScreenerSnapshot).where(StockScreenerSnapshot.date == as_of_date)
        )
        snapshot_rows = snapshot_result.scalars().all()
        snapshots = pd.DataFrame([
            {
                "ticker": row.ticker,
                "date": row.date,
                "sector": row.sector,
                "pe_ratio": float(row.pe_ratio) if row.pe_ratio is not None else None,
                "pb_ratio": float(row.pb_ratio) if row.pb_ratio is not None else None,
                "roe": float(row.roe) if row.roe is not None else None,
                "gross_margin": float(row.gross_margin) if row.gross_margin is not None else None,
                "debt_to_equity": float(row.debt_to_equity) if row.debt_to_equity is not None else None,
                "sales_growth_5yr": float(row.sales_growth_5yr) if row.sales_growth_5yr is not None else None,
            }
            for row in snapshot_rows
        ])
        if snapshots.empty:
            raise ValueError(f"No screener snapshot found for {as_of_date}")
        prices = await _load_price_frame(db, snapshots["ticker"], as_of_date)
        factors = compute_factor_frame(snapshots, prices, as_of_date)
        raw_columns = [f"{name}_raw" for name in FACTOR_NAMES if name != "composite"]
        coverage = float((factors[raw_columns].notna().sum(axis=1) >= 3).mean()) if not factors.empty else 0.0
        quality = {"passed": coverage >= 0.8, "metrics": {"tickers": len(factors), "composite_coverage": coverage}}
        if not quality["passed"]:
            raise ValueError(f"Factor coverage below 80%: {coverage:.2%}")

        await db.execute(
            delete(FactorValue).where(FactorValue.as_of_date == as_of_date, FactorValue.version == FACTOR_VERSION)
        )
        # Factor inputs become tradable no earlier than the quality-gated
        # screener publication. Never backdate availability to the price date.
        available_at = screener_publication.published_at
        values = []
        for _, row in factors.iterrows():
            for factor_name in FACTOR_NAMES:
                raw = row.get(f"{factor_name}_raw", row.get(factor_name))
                normalized = row.get(factor_name)
                values.append({
                    "ticker": row["ticker"],
                    "as_of_date": as_of_date,
                    "factor_name": factor_name,
                    "raw_value": None if pd.isna(raw) else float(raw),
                    "normalized_value": None if pd.isna(normalized) else float(normalized),
                    "version": FACTOR_VERSION,
                    "available_at": available_at,
                    "source_run_id": run_id,
                    "details": {"sector": row.get("sector") or "Unknown"},
                })
        for start in range(0, len(values), 1000):
            stmt = insert(FactorValue).values(values[start:start + 1000])
            stmt = stmt.on_conflict_do_update(
                index_elements=["ticker", "as_of_date", "factor_name", "version"],
                set_={
                    "raw_value": stmt.excluded.raw_value,
                    "normalized_value": stmt.excluded.normalized_value,
                    "available_at": stmt.excluded.available_at,
                    "source_run_id": run_id,
                    "details": stmt.excluded.details,
                },
            )
            await db.execute(stmt)
        await db.commit()
        await publish_dataset("factors", as_of_date, run_id)
        await finish_pipeline_run(run_id, "published", quality_report=quality)
        return {"run_id": run_id, "as_of_date": as_of_date.isoformat(), "version": FACTOR_VERSION, **quality["metrics"]}
    except Exception as exc:
        await db.rollback()
        await finish_pipeline_run(run_id, "failed", error_message=str(exc))
        raise


async def compute_latest_factors() -> dict:
    async with async_session_maker() as db:
        result = await db.execute(
            select(DataPublication.as_of_date)
            .where(DataPublication.dataset == "screener", DataPublication.status == "published")
            .order_by(DataPublication.as_of_date.desc())
            .limit(1)
        )
        as_of_date = result.scalar_one_or_none()
        if as_of_date is None:
            raise ValueError("No published screener snapshot is available")
        return await compute_and_store_factors(db, as_of_date)


async def compute_factors_for_date(as_of_date: date) -> dict:
    """Compute an exact published screener date instead of whichever is latest."""
    async with async_session_maker() as db:
        return await compute_and_store_factors(db, as_of_date)
