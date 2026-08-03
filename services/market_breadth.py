"""Point-in-time market breadth calculation, publication, and serving."""

from __future__ import annotations

import asyncio
import math
from datetime import date, timedelta
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd
from sqlalchemy import delete, desc, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.trading_calendar import is_us_market_session, latest_completed_us_session
from database import async_session_maker
from models import (
    DailyPrice,
    DataPublication,
    MarketBreadthSnapshot,
    PipelineRun,
    RRGPriceSnapshot,
    UniverseMembership,
)
from services.history_backfill import backfill_price_history
from services.pipeline_runs import (
    begin_pipeline_run,
    finish_pipeline_run,
    publish_datasets_and_finish,
    update_pipeline_run,
)
from services.rrg_prices import (
    RRG_BENCHMARK,
    RRG_PRICE_HISTORY_DATASET,
    RRG_PRICE_TICKERS,
    RRG_SECTOR_NAMES,
)
from services.universe import (
    HISTORICAL_UNIVERSE_DATASET,
    HISTORICAL_UNIVERSE_SOURCE,
    historical_universe_tickers,
)


MARKET_BREADTH_DATASET = "market_breadth"
MARKET_BREADTH_DISPLAY_SESSIONS = 252
MARKET_BREADTH_PRICE_SESSIONS = 504
MARKET_BREADTH_RETENTION_RUNS = 5
MARKET_UNIVERSES = ("SP500", "RUSSELL2000", "SP500_RUSSELL2000")
RSP_TICKER = "RSP.US"
PERIOD_SESSIONS = {"3m": 63, "6m": 126, "1y": 252}


class MarketOverviewUnavailable(RuntimeError):
    pass


def effective_close(adjusted_close: Any, close: Any) -> Optional[float]:
    """Prefer adjusted close and fall back only when it is unavailable."""
    selected = adjusted_close if adjusted_close is not None else close
    if selected is None:
        return None
    try:
        value = float(selected)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def market_sessions_through(target: date, count: int) -> list[date]:
    sessions: list[date] = []
    cursor = target
    while len(sessions) < count:
        if is_us_market_session(cursor):
            sessions.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(sessions))


def build_price_feature_frame(
    price_rows: Iterable[dict],
    expected_dates: Optional[Iterable[date]] = None,
) -> pd.DataFrame:
    frame = pd.DataFrame(price_rows)
    if frame.empty:
        return frame
    frame["date"] = pd.to_datetime(frame["date"])
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna(subset=["ticker", "date", "close"])
    frame = frame[frame["close"] > 0].sort_values(["ticker", "date"])
    frame = frame.drop_duplicates(["ticker", "date"], keep="last").reset_index(drop=True)
    grouped = frame.groupby("ticker", group_keys=False)["close"]
    frame["return_1d"] = grouped.pct_change(fill_method=None)
    session_dates = sorted(
        {
            pd.Timestamp(value).normalize()
            for value in (
                expected_dates if expected_dates is not None else frame["date"].unique()
            )
        }
    )
    expected_previous = {
        current: previous
        for previous, current in zip(session_dates, session_dates[1:])
    }
    prior_observation_date = frame.groupby("ticker")["date"].shift(1)
    immediately_previous = prior_observation_date.eq(
        frame["date"].map(expected_previous)
    )
    frame.loc[~immediately_previous, "return_1d"] = np.nan
    for window in (20, 50, 200):
        frame[f"ma{window}"] = grouped.transform(
            lambda values, size=window: values.rolling(size, min_periods=size).mean()
        )
    frame["prior_high_251"] = grouped.transform(
        lambda values: values.shift(1).rolling(251, min_periods=251).max()
    )
    frame["prior_low_251"] = grouped.transform(
        lambda values: values.shift(1).rolling(251, min_periods=251).min()
    )
    return frame


def _members_as_of(intervals: list[dict], as_of_date: date) -> set[str]:
    return {
        interval["ticker"]
        for interval in intervals
        if interval["effective_from"] <= as_of_date
        and (
            interval["effective_to"] is None
            or interval["effective_to"] >= as_of_date
        )
    }


def calculate_market_breadth_rows(
    price_frame: pd.DataFrame,
    memberships: dict[str, list[dict]],
    display_dates: list[date],
) -> list[dict]:
    """Calculate raw breadth counts without introducing current-member look-ahead."""
    if price_frame.empty:
        return []
    frame = price_frame.copy()
    frame["date_key"] = frame["date"].dt.date
    by_date = {
        price_date: values.set_index("ticker", drop=False)
        for price_date, values in frame.groupby("date_key")
    }
    rows: list[dict] = []
    for as_of_date in display_dates:
        sp500_members = _members_as_of(memberships.get("SP500", []), as_of_date)
        russell_members = _members_as_of(
            memberships.get("RUSSELL2000", []),
            as_of_date,
        )
        members_by_universe = {
            "SP500": sp500_members,
            "RUSSELL2000": russell_members,
            "SP500_RUSSELL2000": sp500_members | russell_members,
        }
        day_frame = by_date.get(as_of_date)
        for universe in MARKET_UNIVERSES:
            members = members_by_universe[universe]
            if day_frame is None:
                selected = pd.DataFrame(columns=frame.columns)
            else:
                selected = day_frame.reindex(sorted(members))
            closes = pd.to_numeric(selected.get("close"), errors="coerce")
            valid_prices = closes.notna() & np.isfinite(closes) & (closes > 0)
            returns = pd.to_numeric(selected.get("return_1d"), errors="coerce")
            valid_returns = returns[returns.notna() & np.isfinite(returns)]
            unchanged_mask = valid_returns.abs() <= 1e-12

            row: dict[str, Any] = {
                "universe": universe,
                "date": as_of_date,
                "member_count": len(members),
                "price_count": int(valid_prices.sum()),
                "return_count": int(len(valid_returns)),
                "advances": int((valid_returns > 1e-12).sum()),
                "declines": int((valid_returns < -1e-12).sum()),
                "unchanged": int(unchanged_mask.sum()),
            }
            for window in (20, 50, 200):
                ma = pd.to_numeric(selected.get(f"ma{window}"), errors="coerce")
                eligible = valid_prices & ma.notna() & np.isfinite(ma) & (ma > 0)
                row[f"ma{window}_eligible"] = int(eligible.sum())
                row[f"above_ma{window}"] = int((eligible & (closes > ma)).sum())

            prior_high = pd.to_numeric(selected.get("prior_high_251"), errors="coerce")
            prior_low = pd.to_numeric(selected.get("prior_low_251"), errors="coerce")
            high_low_eligible = (
                valid_prices
                & prior_high.notna()
                & np.isfinite(prior_high)
                & prior_low.notna()
                & np.isfinite(prior_low)
            )
            row["high_low_eligible"] = int(high_low_eligible.sum())
            row["new_high_count"] = int(
                (high_low_eligible & (closes > prior_high)).sum()
            )
            row["new_low_count"] = int(
                (high_low_eligible & (closes < prior_low)).sum()
            )
            row["dispersion_1d"] = (
                float(np.std(valid_returns.to_numpy(dtype=float), ddof=0))
                if len(valid_returns) >= 2
                else None
            )
            rows.append(row)
    return rows


def validate_market_breadth_rows(rows: list[dict]) -> dict:
    expected_rows = len(MARKET_UNIVERSES) * MARKET_BREADTH_DISPLAY_SESSIONS
    errors: list[str] = []
    warnings: list[str] = []
    if len(rows) != expected_rows:
        errors.append(f"expected {expected_rows} breadth rows, found {len(rows)}")

    minimum_members = {
        "SP500": settings.PIPELINE_MIN_SP500_SIZE,
        "RUSSELL2000": settings.PIPELINE_MIN_RUSSELL2000_SIZE,
        "SP500_RUSSELL2000": max(
            settings.PIPELINE_MIN_RUSSELL2000_SIZE,
            settings.PIPELINE_MIN_UNIVERSE_SIZE,
        ),
    }
    minimum_price_coverage = 1.0
    minimum_long_coverage = 1.0
    for row in rows:
        members = row["member_count"]
        if members < minimum_members[row["universe"]]:
            errors.append(
                f"{row['universe']} {row['date']} has only {members} members"
            )
            continue
        price_coverage = row["price_count"] / members if members else 0.0
        return_coverage = row["return_count"] / members if members else 0.0
        minimum_price_coverage = min(
            minimum_price_coverage,
            price_coverage,
            return_coverage,
        )
        if min(price_coverage, return_coverage) < settings.PIPELINE_MIN_PRICE_COVERAGE:
            errors.append(
                f"{row['universe']} {row['date']} price/return coverage below "
                f"{settings.PIPELINE_MIN_PRICE_COVERAGE:.0%}"
            )
        for field_name in ("ma20_eligible", "ma50_eligible", "ma200_eligible", "high_low_eligible"):
            coverage = row[field_name] / members if members else 0.0
            minimum_long_coverage = min(minimum_long_coverage, coverage)
            if coverage < settings.PIPELINE_MIN_PRICE_FACTOR_COVERAGE:
                errors.append(
                    f"{row['universe']} {row['date']} {field_name} coverage below "
                    f"{settings.PIPELINE_MIN_PRICE_FACTOR_COVERAGE:.0%}"
                )
        dispersion = row.get("dispersion_1d")
        if dispersion is None or not math.isfinite(dispersion) or dispersion < 0:
            errors.append(f"{row['universe']} {row['date']} has invalid dispersion")
    if minimum_long_coverage < 0.90:
        warnings.append(
            f"minimum long-window eligibility is {minimum_long_coverage:.2%}"
        )
    return {
        "passed": not errors,
        "metrics": {
            "rows": len(rows),
            "sessions": MARKET_BREADTH_DISPLAY_SESSIONS,
            "universes": list(MARKET_UNIVERSES),
            "minimum_price_coverage": minimum_price_coverage,
            "minimum_long_window_coverage": minimum_long_coverage,
        },
        "errors": errors,
        "warnings": warnings,
    }


async def _published_run_for_date(
    db: AsyncSession,
    dataset: str,
    as_of_date: date,
) -> Optional[int]:
    result = await db.execute(
        select(DataPublication.pipeline_run_id)
        .join(PipelineRun, PipelineRun.id == DataPublication.pipeline_run_id)
        .where(
            DataPublication.dataset == dataset,
            DataPublication.as_of_date == as_of_date,
            DataPublication.status == "published",
            PipelineRun.status == "published",
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def backfill_market_breadth_price_history(target_date: date) -> dict:
    display_dates = market_sessions_through(
        target_date,
        MARKET_BREADTH_DISPLAY_SESSIONS,
    )
    async with async_session_maker() as db:
        tickers = await historical_universe_tickers(
            db,
            ("SP500", "RUSSELL2000"),
            display_dates[0],
            target_date,
        )
    if not tickers:
        raise ValueError("No point-in-time index members are available for breadth backfill")
    return await backfill_price_history(
        tickers,
        history_days=MARKET_BREADTH_PRICE_SESSIONS - 1,
        target_date=target_date,
        include_corporate_actions=False,
        minimum_ticker_coverage=settings.PIPELINE_MIN_PRICE_FACTOR_COVERAGE,
        include_target_session=True,
        publish_dataset=False,
    )


async def refresh_market_breadth(target_date: date) -> dict:
    target = (
        target_date
        if is_us_market_session(target_date)
        else latest_completed_us_session(target_date)
    )
    async with async_session_maker() as dependency_db:
        missing = [
            dataset
            for dataset in (
                "price_history",
                HISTORICAL_UNIVERSE_DATASET,
                RRG_PRICE_HISTORY_DATASET,
            )
            if await _published_run_for_date(dependency_db, dataset, target) is None
        ]
    if missing:
        return {
            "status": "deferred",
            "reason": "missing-publications",
            "missing": missing,
            "as_of_date": target.isoformat(),
        }

    async with async_session_maker() as existing_db:
        existing_run = await _published_run_for_date(
            existing_db,
            MARKET_BREADTH_DATASET,
            target,
        )
        if existing_run is not None:
            count = await existing_db.scalar(
                select(MarketBreadthSnapshot.id).where(
                    MarketBreadthSnapshot.pipeline_run_id == existing_run
                ).limit(1)
            )
            if count is not None:
                return {
                    "status": "skipped",
                    "reason": "already-published",
                    "as_of_date": target.isoformat(),
                }

    full_dates = market_sessions_through(target, MARKET_BREADTH_PRICE_SESSIONS)
    display_dates = full_dates[-MARKET_BREADTH_DISPLAY_SESSIONS:]
    async with async_session_maker() as source_db:
        membership_result = await source_db.execute(
            select(UniverseMembership).where(
                UniverseMembership.universe.in_(("SP500", "RUSSELL2000")),
                UniverseMembership.source == HISTORICAL_UNIVERSE_SOURCE,
                UniverseMembership.effective_from <= target,
                (
                    UniverseMembership.effective_to.is_(None)
                    | (UniverseMembership.effective_to >= display_dates[0])
                ),
            )
        )
        memberships: dict[str, list[dict]] = {"SP500": [], "RUSSELL2000": []}
        for membership in membership_result.scalars():
            memberships[membership.universe].append({
                "ticker": membership.ticker,
                "effective_from": membership.effective_from,
                "effective_to": membership.effective_to,
            })
        tickers = sorted({
            interval["ticker"]
            for intervals in memberships.values()
            for interval in intervals
        })
        price_rows: list[dict] = []
        for start in range(0, len(tickers), 500):
            result = await source_db.execute(
                select(
                    DailyPrice.ticker,
                    DailyPrice.date,
                    DailyPrice.adjusted_close,
                    DailyPrice.close,
                ).where(
                    DailyPrice.ticker.in_(tickers[start:start + 500]),
                    DailyPrice.date >= full_dates[0],
                    DailyPrice.date <= target,
                )
            )
            price_rows.extend(
                {
                    "ticker": ticker,
                    "date": price_date,
                    "close": effective_close(adjusted_close, close),
                }
                for ticker, price_date, adjusted_close, close in result.all()
            )

    run_id = await begin_pipeline_run("market_breadth", target)
    try:
        await update_pipeline_run(run_id, "calculating_breadth")
        price_frame = await asyncio.to_thread(
            build_price_feature_frame,
            price_rows,
            full_dates,
        )
        rows = await asyncio.to_thread(
            calculate_market_breadth_rows,
            price_frame,
            memberships,
            display_dates,
        )
        quality = validate_market_breadth_rows(rows)
        if not quality["passed"]:
            raise ValueError("Market breadth quality gate failed: " + "; ".join(quality["errors"][:10]))
        snapshot_rows = [
            {**row, "pipeline_run_id": run_id}
            for row in rows
        ]
        await update_pipeline_run(run_id, "publishing_breadth", len(snapshot_rows))
        async with async_session_maker() as db, db.begin():
            for start in range(0, len(snapshot_rows), 500):
                await db.execute(
                    insert(MarketBreadthSnapshot).values(
                        snapshot_rows[start:start + 500]
                    )
                )
            await publish_datasets_and_finish(
                db,
                [MARKET_BREADTH_DATASET],
                target,
                run_id,
                quality_report=quality,
                records_processed=len(snapshot_rows),
            )
            expired_result = await db.execute(
                select(DataPublication)
                .where(DataPublication.dataset == MARKET_BREADTH_DATASET)
                .order_by(desc(DataPublication.as_of_date))
                .offset(MARKET_BREADTH_RETENTION_RUNS)
            )
            expired = list(expired_result.scalars())
            expired_run_ids = [publication.pipeline_run_id for publication in expired]
            if expired_run_ids:
                await db.execute(
                    delete(MarketBreadthSnapshot).where(
                        MarketBreadthSnapshot.pipeline_run_id.in_(expired_run_ids)
                    )
                )
                for publication in expired:
                    await db.delete(publication)
            retained_run_ids = list((await db.execute(
                select(DataPublication.pipeline_run_id).where(
                    DataPublication.dataset == MARKET_BREADTH_DATASET
                )
            )).scalars())
            if retained_run_ids:
                await db.execute(
                    delete(MarketBreadthSnapshot).where(
                        MarketBreadthSnapshot.pipeline_run_id.not_in(retained_run_ids)
                    )
                )
        return {
            "run_id": run_id,
            "status": "published",
            "as_of_date": target.isoformat(),
            **quality["metrics"],
        }
    except asyncio.CancelledError:
        await finish_pipeline_run(run_id, "cancelled")
        raise
    except Exception as exc:
        await finish_pipeline_run(
            run_id,
            "failed",
            quality_report=quality if "quality" in locals() else None,
            error_message=str(exc),
        )
        raise


def _percentage(numerator: int, denominator: int) -> Optional[float]:
    return 100.0 * numerator / denominator if denominator else None


def _normalized_index(values: list[float]) -> list[float]:
    if not values or values[0] <= 0:
        raise MarketOverviewUnavailable("Market price series has no valid base value")
    base = values[0]
    return [100.0 * value / base for value in values]


async def get_market_overview(
    db: AsyncSession,
    universe: str,
    period: str,
) -> dict:
    sessions = PERIOD_SESSIONS[period]
    publication_result = await db.execute(
        select(DataPublication)
        .join(PipelineRun, PipelineRun.id == DataPublication.pipeline_run_id)
        .where(
            DataPublication.dataset == MARKET_BREADTH_DATASET,
            DataPublication.status == "published",
            PipelineRun.status == "published",
        )
        .order_by(desc(DataPublication.as_of_date))
        .limit(MARKET_BREADTH_RETENTION_RUNS)
    )
    market_publications = list(publication_result.scalars())
    selected_publication = None
    rrg_run_id = None
    for publication in market_publications:
        candidate = await _published_run_for_date(
            db,
            RRG_PRICE_HISTORY_DATASET,
            publication.as_of_date,
        )
        if candidate is not None:
            selected_publication = publication
            rrg_run_id = candidate
            break
    if selected_publication is None or rrg_run_id is None:
        raise MarketOverviewUnavailable("Market overview has not been published yet")

    breadth_result = await db.execute(
        select(MarketBreadthSnapshot)
        .where(
            MarketBreadthSnapshot.pipeline_run_id
            == selected_publication.pipeline_run_id,
            MarketBreadthSnapshot.universe == universe,
        )
        .order_by(MarketBreadthSnapshot.date.asc())
    )
    full_breadth = list(breadth_result.scalars())
    if len(full_breadth) < sessions:
        raise MarketOverviewUnavailable(
            f"Only {len(full_breadth)} breadth sessions are available for {period}"
        )
    selected_breadth = full_breadth[-sessions:]
    selected_dates = [row.date for row in selected_breadth]

    price_result = await db.execute(
        select(RRGPriceSnapshot)
        .where(
            RRGPriceSnapshot.pipeline_run_id == rrg_run_id,
            RRGPriceSnapshot.ticker.in_(RRG_PRICE_TICKERS),
            RRGPriceSnapshot.date.in_(selected_dates),
        )
        .order_by(RRGPriceSnapshot.date.asc())
    )
    prices: dict[str, dict[date, float]] = {}
    for row in price_result.scalars():
        prices.setdefault(row.ticker, {})[row.date] = float(row.close)
    required_tickers = {*RRG_SECTOR_NAMES, RRG_BENCHMARK, RSP_TICKER}
    missing_tickers = [
        ticker
        for ticker in sorted(required_tickers)
        if set(prices.get(ticker, {})) != set(selected_dates)
    ]
    if missing_tickers:
        raise MarketOverviewUnavailable(
            "Market ETF history is incomplete for: " + ", ".join(missing_tickers)
        )

    spy_values = [prices[RRG_BENCHMARK][item] for item in selected_dates]
    sector_trends = []
    for ticker, label in RRG_SECTOR_NAMES.items():
        values = [prices[ticker][item] for item in selected_dates]
        relative_values = [value / spy for value, spy in zip(values, spy_values)]
        sector_trends.append({
            "ticker": ticker,
            "label": label,
            "absolute_index": _normalized_index(values),
            "relative_to_spy_index": _normalized_index(relative_values),
        })
    rsp_values = [prices[RSP_TICKER][item] for item in selected_dates]
    rsp_spy_index = _normalized_index([
        rsp / spy for rsp, spy in zip(rsp_values, spy_values)
    ])

    net_advances_full = [
        _percentage(row.advances - row.declines, row.return_count)
        for row in full_breadth
    ]
    net_series = pd.Series(net_advances_full, dtype="float64")
    mcclellan_full = (
        net_series.ewm(span=19, adjust=False, min_periods=19).mean()
        - net_series.ewm(span=39, adjust=False, min_periods=39).mean()
    )
    dispersion_full = pd.Series(
        [row.dispersion_1d for row in full_breadth],
        dtype="float64",
    )
    dispersion_20_full = dispersion_full.ewm(
        span=20,
        adjust=False,
        min_periods=20,
    ).mean()
    start_index = len(full_breadth) - sessions

    quality_report = (
        await db.scalar(
            select(PipelineRun.quality_report).where(
                PipelineRun.id == selected_publication.pipeline_run_id
            )
        )
    ) or {}
    expected_date = latest_completed_us_session(date.today())
    warnings = list(quality_report.get("warnings") or [])
    return {
        "meta": {
            "universe": universe,
            "period": period,
            "as_of_date": selected_publication.as_of_date.isoformat(),
            "expected_as_of_date": expected_date.isoformat(),
            "published_at": selected_publication.published_at.isoformat(),
            "stale": selected_publication.as_of_date < expected_date,
            "membership_mode": "point_in_time",
            "data_complete": True,
            "warnings": warnings,
        },
        "dates": [item.isoformat() for item in selected_dates],
        "benchmark": {
            "ticker": RRG_BENCHMARK,
            "absolute_index": _normalized_index(spy_values),
        },
        "sector_trends": sector_trends,
        "rsp_spy_index": rsp_spy_index,
        "breadth": {
            "pct_above_ma20": [
                _percentage(row.above_ma20, row.ma20_eligible)
                for row in selected_breadth
            ],
            "pct_above_ma50": [
                _percentage(row.above_ma50, row.ma50_eligible)
                for row in selected_breadth
            ],
            "pct_above_ma200": [
                _percentage(row.above_ma200, row.ma200_eligible)
                for row in selected_breadth
            ],
            "net_advances_pct": net_advances_full[-sessions:],
            "new_high_low_pct": [
                _percentage(
                    row.new_high_count - row.new_low_count,
                    row.high_low_eligible,
                )
                for row in selected_breadth
            ],
            "new_high_pct": [
                _percentage(row.new_high_count, row.high_low_eligible)
                for row in selected_breadth
            ],
            "new_low_pct": [
                _percentage(row.new_low_count, row.high_low_eligible)
                for row in selected_breadth
            ],
            "mcclellan": [
                None if pd.isna(value) else float(value)
                for value in mcclellan_full.iloc[start_index:]
            ],
            "dispersion_1d": [
                row.dispersion_1d for row in selected_breadth
            ],
            "dispersion_20d": [
                None if pd.isna(value) else float(value)
                for value in dispersion_20_full.iloc[start_index:]
            ],
            "member_count": [row.member_count for row in selected_breadth],
            "price_coverage_pct": [
                _percentage(row.price_count, row.member_count)
                for row in selected_breadth
            ],
        },
    }
