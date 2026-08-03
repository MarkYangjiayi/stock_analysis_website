import asyncio
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable, List, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.trading_calendar import is_us_market_session, latest_completed_us_session
from database import async_session_maker
from models import UniverseMembership
from services import eodhd_client
from services.pipeline_runs import (
    begin_pipeline_run,
    finish_pipeline_run,
    latest_published_date,
    publish_datasets_and_finish,
    update_pipeline_run,
)
from services.raw_store import persist_snapshot


HISTORICAL_UNIVERSE_DATASET = "universe_history"
HISTORICAL_UNIVERSE_SOURCE = "EODHD HistoricalTickerComponents"
LIVE_UNIVERSE_SOURCE = "EODHD Live Index Components"
HISTORICAL_UNIVERSE_REQUIRED_SESSIONS = 252
INDEX_UNIVERSES = {
    "SP500": "GSPC.INDX",
    "RUSSELL2000": "RUT.INDX",
}


def _parse_date(value: Any, field_name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field_name}: {value!r}") from exc


def parse_historical_memberships(
    rows: Iterable[dict],
    universe: str,
    *,
    required_from: Optional[date] = None,
) -> list[dict]:
    """Normalize provider intervals and reject ambiguity in the required window."""
    if universe not in INDEX_UNIVERSES:
        raise ValueError(f"Unsupported historical universe: {universe}")

    normalized_by_key: dict[tuple[str, date], dict] = {}
    for row in rows:
        code = str(row.get("Code") or "").strip().upper()
        if not code:
            raise ValueError(f"{universe} history contains a row without Code")
        ticker = code if "." in code else f"{code}.US"
        raw_end = row.get("EndDate")
        effective_to = None if str(raw_end).strip().lower() in {
            "none",
            "",
            "null",
            "0000-00-00",
        } else _parse_date(raw_end, "EndDate")
        raw_start = row.get("StartDate")
        if raw_start in (None, "", "null"):
            if (
                required_from is not None
                and effective_to is not None
                and effective_to < required_from
            ):
                # The provider sometimes omits the original join date for
                # ancient constituents. It is safe to exclude them only when
                # their known exit predates every date this publication serves.
                continue
            raise ValueError(
                f"{universe} {ticker} has no StartDate inside the required history window"
            )
        effective_from = _parse_date(raw_start, "StartDate")
        if effective_to is not None and effective_to < effective_from:
            raise ValueError(
                f"{universe} {ticker} ends before it starts: "
                f"{effective_from}..{effective_to}"
            )
        key = (ticker, effective_from)
        candidate = {
            "universe": universe,
            "ticker": ticker,
            "effective_from": effective_from,
            "effective_to": effective_to,
            "source": HISTORICAL_UNIVERSE_SOURCE,
        }
        previous = normalized_by_key.get(key)
        if previous is not None and previous["effective_to"] != effective_to:
            raise ValueError(f"Conflicting {universe} interval for {ticker} on {effective_from}")
        normalized_by_key[key] = candidate

    intervals = sorted(
        normalized_by_key.values(),
        key=lambda item: (item["ticker"], item["effective_from"]),
    )
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for interval in intervals:
        by_ticker[interval["ticker"]].append(interval)
    for ticker, ticker_intervals in by_ticker.items():
        previous_end: Optional[date] = None
        previous_open = False
        for index, interval in enumerate(ticker_intervals):
            if index and (
                previous_open
                or previous_end is None
                or interval["effective_from"] <= previous_end
            ):
                raise ValueError(f"Overlapping {universe} membership intervals for {ticker}")
            previous_end = interval["effective_to"]
            previous_open = previous_end is None
    if not intervals:
        raise ValueError(f"{universe} historical membership response is empty")
    return intervals


def historical_membership_required_from(target: date) -> date:
    sessions: list[date] = []
    cursor = target
    while len(sessions) < HISTORICAL_UNIVERSE_REQUIRED_SESSIONS:
        if is_us_market_session(cursor):
            sessions.append(cursor)
        cursor -= timedelta(days=1)
    return sessions[-1]


def validate_historical_memberships(
    intervals: list[dict],
    universe: str,
    as_of_date: date,
) -> dict:
    minimum_size = (
        settings.PIPELINE_MIN_SP500_SIZE
        if universe == "SP500"
        else settings.PIPELINE_MIN_RUSSELL2000_SIZE
    )
    active = {
        interval["ticker"]
        for interval in intervals
        if interval["effective_from"] <= as_of_date
        and (
            interval["effective_to"] is None
            or interval["effective_to"] >= as_of_date
        )
    }
    if len(active) < minimum_size:
        raise ValueError(
            f"{universe} historical membership has only {len(active)} active "
            f"constituents; expected at least {minimum_size}"
        )
    return {
        "intervals": len(intervals),
        "tickers": len({interval["ticker"] for interval in intervals}),
        "active": len(active),
        "minimum_active": minimum_size,
    }


async def replace_historical_memberships(
    db: AsyncSession,
    universe: str,
    intervals: list[dict],
    source_run_id: int,
) -> None:
    """Replace only provider-owned rows after the caller validates the payload."""
    await db.execute(
        delete(UniverseMembership).where(
            UniverseMembership.universe == universe,
            UniverseMembership.source.in_(("EODHD", HISTORICAL_UNIVERSE_SOURCE)),
        )
    )
    values = [
        {**interval, "source_run_id": source_run_id}
        for interval in intervals
    ]
    for start in range(0, len(values), 1000):
        await db.execute(insert(UniverseMembership).values(values[start:start + 1000]))


async def refresh_historical_universe_memberships(
    target_date: Optional[date] = None,
) -> dict:
    """Atomically publish complete S&P 500 and Russell 2000 membership history."""
    reference = target_date or date.today()
    target = (
        reference
        if target_date is not None and is_us_market_session(reference)
        else latest_completed_us_session(reference)
    )
    published = await latest_published_date(HISTORICAL_UNIVERSE_DATASET)
    if published is not None and published >= target:
        return {
            "status": "skipped",
            "reason": "already-published",
            "as_of_date": published.isoformat(),
        }
    run_id = await begin_pipeline_run("historical_universe_sync", target)
    try:
        await update_pipeline_run(run_id, "fetching_history")
        async with eodhd_client.create_http_client() as client:
            payloads = await asyncio.gather(
                *(
                    eodhd_client.get_index_component_history(index_ticker, client=client)
                    for index_ticker in INDEX_UNIVERSES.values()
                )
            )
        rows_by_universe = dict(zip(INDEX_UNIVERSES, payloads))
        normalized: dict[str, list[dict]] = {}
        quality_metrics: dict[str, dict] = {}
        required_from = historical_membership_required_from(target)
        for universe, rows in rows_by_universe.items():
            if rows is None:
                raise ValueError(f"Provider failed to return {universe} membership history")
            intervals = parse_historical_memberships(
                rows,
                universe,
                required_from=required_from,
            )
            normalized[universe] = intervals
            quality_metrics[universe] = validate_historical_memberships(
                intervals,
                universe,
                target,
            )
            quality_metrics[universe]["required_from"] = required_from.isoformat()
            quality_metrics[universe]["excluded_or_deduplicated_source_rows"] = (
                len(rows) - len(intervals)
            )

        await update_pipeline_run(run_id, "publishing_history")
        async with async_session_maker() as db, db.begin():
            for universe, intervals in normalized.items():
                await persist_snapshot(
                    db,
                    "EODHD",
                    "index_membership_history",
                    rows_by_universe[universe],
                    as_of_date=target,
                    details={
                        "universe": universe,
                        "index_ticker": INDEX_UNIVERSES[universe],
                    },
                )
                await replace_historical_memberships(
                    db,
                    universe,
                    intervals,
                    source_run_id=run_id,
                )
            quality = {"passed": True, "metrics": quality_metrics}
            await publish_datasets_and_finish(
                db,
                [HISTORICAL_UNIVERSE_DATASET],
                target,
                run_id,
                quality_report=quality,
                records_processed=sum(len(rows) for rows in normalized.values()),
            )
        return {
            "run_id": run_id,
            "status": "published",
            "as_of_date": target.isoformat(),
            "universes": quality_metrics,
        }
    except asyncio.CancelledError:
        await finish_pipeline_run(run_id, "cancelled")
        raise
    except Exception as exc:
        await finish_pipeline_run(run_id, "failed", error_message=str(exc))
        raise


async def historical_universe_tickers(
    db: AsyncSession,
    universes: Iterable[str],
    start_date: date,
    end_date: date,
) -> list[str]:
    result = await db.execute(
        select(UniverseMembership.ticker)
        .where(
            UniverseMembership.universe.in_(set(universes)),
            UniverseMembership.source == HISTORICAL_UNIVERSE_SOURCE,
            UniverseMembership.effective_from <= end_date,
            (
                UniverseMembership.effective_to.is_(None)
                | (UniverseMembership.effective_to >= start_date)
            ),
        )
        .distinct()
    )
    return sorted(set(result.scalars()))


async def record_universe_membership(
    db: AsyncSession,
    universe: str,
    tickers: Iterable[str],
    effective_date: date,
    source_run_id: Optional[int] = None,
    minimum_retained_fraction: Optional[float] = None,
    known_exits: Optional[Iterable[str]] = None,
    source: str = "EODHD",
) -> None:
    current = {ticker.upper() for ticker in tickers}
    allowed_exits = (
        {ticker.upper() for ticker in known_exits} - current
        if known_exits is not None
        else set()
    )
    result = await db.execute(
        select(UniverseMembership).where(
            UniverseMembership.universe == universe,
            UniverseMembership.source == source,
            UniverseMembership.effective_to.is_(None),
        )
    )
    active = {row.ticker: row for row in result.scalars().all()}
    if minimum_retained_fraction is not None:
        if not 0 < minimum_retained_fraction <= 1:
            raise ValueError("minimum_retained_fraction must be in (0, 1]")
        if active:
            protected_active = set(active) - allowed_exits
            retained = (
                len(protected_active & current) / len(protected_active)
                if protected_active
                else 1.0
            )
            if retained < minimum_retained_fraction:
                raise ValueError(
                    f"Observed {universe} universe retained only {retained:.2%} of "
                    f"{len(protected_active)} active constituents after known exits; "
                    "refusing to close memberships"
                )
    exited = set(active) - current
    if exited:
        same_day_exits = {
            ticker
            for ticker in exited
            if active[ticker].effective_from == effective_date
        }
        if same_day_exits:
            await db.execute(
                delete(UniverseMembership).where(
                    UniverseMembership.universe == universe,
                    UniverseMembership.source == source,
                    UniverseMembership.ticker.in_(same_day_exits),
                    UniverseMembership.effective_from == effective_date,
                )
            )
        earlier_exits = exited - same_day_exits
        if earlier_exits:
            await db.execute(
                update(UniverseMembership)
                .where(
                    UniverseMembership.universe == universe,
                    UniverseMembership.source == source,
                    UniverseMembership.ticker.in_(earlier_exits),
                    UniverseMembership.effective_to.is_(None),
                )
                .values(effective_to=effective_date - timedelta(days=1))
            )
    new_rows = [
        {
            "universe": universe,
            "ticker": ticker,
            "effective_from": effective_date,
            "source": source,
            "source_run_id": source_run_id,
        }
        for ticker in current - set(active)
    ]
    if new_rows:
        await db.execute(
            insert(UniverseMembership).values(new_rows).on_conflict_do_nothing(
                index_elements=["universe", "ticker", "effective_from", "source"]
            )
        )


async def universe_as_of(db: AsyncSession, universe: str, as_of_date: date) -> List[str]:
    result = await db.execute(
        select(UniverseMembership.ticker).where(
            UniverseMembership.universe == universe,
            UniverseMembership.effective_from <= as_of_date,
            (UniverseMembership.effective_to.is_(None) | (UniverseMembership.effective_to >= as_of_date)),
        )
    )
    return list(result.scalars().all())
