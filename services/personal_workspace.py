from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    PersonalWatchlistItem,
    PersonalWorkspaceState,
    TickerValuationScenario,
)
from services.security_master import canonicalize_ticker
from core.time_utils import utc_now


SCENARIO_NAMES = ("bear", "base", "bull")
WATCHLIST_LIMIT = 100
WATCHLIST_LIMIT_MESSAGE = f"A personal watchlist can contain at most {WATCHLIST_LIMIT} tickers."


def normalize_watchlist(tickers: Iterable[str]) -> list[str]:
    """Canonicalize, de-duplicate, and bound a personal watchlist."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_ticker in tickers:
        ticker = canonicalize_ticker(raw_ticker)
        if ticker in seen:
            continue
        seen.add(ticker)
        normalized.append(ticker)
        if len(normalized) > WATCHLIST_LIMIT:
            raise ValueError(WATCHLIST_LIMIT_MESSAGE)
    return normalized


async def get_watchlist(db: AsyncSession) -> list[str]:
    result = await db.execute(
        select(PersonalWatchlistItem)
        .order_by(PersonalWatchlistItem.sort_order, PersonalWatchlistItem.id)
    )
    return [row.ticker for row in result.scalars().all()]


async def _ensure_watchlist_state(db: AsyncSession) -> None:
    """Idempotently seed the singleton for metadata-created databases."""
    has_items = bool(await get_watchlist(db))
    values = {
        "id": 1,
        "watchlist_initialized": has_items,
        "updated_at": utc_now(),
    }
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        statement = sqlite_insert(PersonalWorkspaceState).values(**values)
        statement = statement.on_conflict_do_nothing(index_elements=["id"])
        await db.execute(statement)
    elif dialect == "postgresql":
        statement = postgresql_insert(PersonalWorkspaceState).values(**values)
        statement = statement.on_conflict_do_nothing(index_elements=["id"])
        await db.execute(statement)
    else:  # pragma: no cover - supported deployments use SQLite or PostgreSQL
        existing = await db.get(PersonalWorkspaceState, 1)
        if existing is None:
            db.add(PersonalWorkspaceState(**values))
            try:
                await db.flush()
            except IntegrityError:
                await db.rollback()
    if has_items:
        await db.execute(
            update(PersonalWorkspaceState)
            .where(PersonalWorkspaceState.id == 1)
            .values(watchlist_initialized=True, updated_at=utc_now())
        )
    await db.commit()


async def _mark_watchlist_edited(db: AsyncSession) -> None:
    """Record a user edit. Edits update this singleton row first, so concurrent
    edits wait on its lock instead of interleaving."""
    await _ensure_watchlist_state(db)
    await db.execute(
        update(PersonalWorkspaceState)
        .where(PersonalWorkspaceState.id == 1)
        .values(watchlist_initialized=True, updated_at=utc_now())
    )


async def replace_watchlist(db: AsyncSession, tickers: Iterable[str]) -> list[str]:
    normalized = normalize_watchlist(tickers)
    await _mark_watchlist_edited(db)
    await db.execute(delete(PersonalWatchlistItem))
    db.add_all(
        PersonalWatchlistItem(ticker=ticker, sort_order=index)
        for index, ticker in enumerate(normalized)
    )
    await db.commit()
    return normalized


async def add_watchlist_item(db: AsyncSession, ticker: str) -> tuple[list[str], bool]:
    """Put one ticker at the front, as the web page does; a listed ticker keeps its place."""
    canonical_ticker = canonicalize_ticker(ticker)
    await _mark_watchlist_edited(db)
    current = await get_watchlist(db)
    if canonical_ticker in current:
        await db.commit()
        return current, False
    if len(current) >= WATCHLIST_LIMIT:
        await db.rollback()
        raise ValueError(WATCHLIST_LIMIT_MESSAGE)
    first_sort_order = await db.scalar(select(func.min(PersonalWatchlistItem.sort_order)))
    db.add(PersonalWatchlistItem(
        ticker=canonical_ticker,
        sort_order=0 if first_sort_order is None else first_sort_order - 1,
    ))
    await db.commit()
    return await get_watchlist(db), True


async def remove_watchlist_item(db: AsyncSession, ticker: str) -> tuple[list[str], bool]:
    canonical_ticker = canonicalize_ticker(ticker)
    await _mark_watchlist_edited(db)
    result = await db.execute(
        delete(PersonalWatchlistItem).where(PersonalWatchlistItem.ticker == canonical_ticker)
    )
    await db.commit()
    return await get_watchlist(db), result.rowcount > 0


async def import_watchlist_if_empty(
    db: AsyncSession,
    tickers: Iterable[str],
) -> tuple[list[str], bool]:
    """Import the legacy browser watchlist once; existing server data always wins."""
    normalized = normalize_watchlist(tickers)
    await _ensure_watchlist_state(db)
    claim = await db.execute(
        update(PersonalWorkspaceState)
        .where(
            PersonalWorkspaceState.id == 1,
            PersonalWorkspaceState.watchlist_initialized.is_(False),
        )
        .values(watchlist_initialized=True, updated_at=utc_now())
    )
    claimed = claim.rowcount == 1
    current = await get_watchlist(db)
    if not claimed:
        await db.commit()
        return current, False

    if current:
        await db.commit()
        return current, False

    db.add_all(
        PersonalWatchlistItem(ticker=ticker, sort_order=index)
        for index, ticker in enumerate(normalized)
    )
    await db.commit()
    return normalized, True


async def get_saved_valuation_scenarios(
    db: AsyncSession,
    ticker: str,
) -> list[dict[str, Any]] | None:
    canonical_ticker = canonicalize_ticker(ticker)
    result = await db.execute(
        select(TickerValuationScenario).where(
            TickerValuationScenario.ticker == canonical_ticker
        )
    )
    by_name = {row.scenario: row for row in result.scalars().all()}
    if set(by_name) != set(SCENARIO_NAMES):
        return None
    # Older saved records allowed scenario-specific discount and terminal rates.
    # Keep each operating-growth view, but normalize legacy records to the base
    # case's company-level capital-market assumptions on read.
    shared_wacc = by_name["base"].wacc
    shared_perpetual_growth = by_name["base"].perpetual_growth
    return [
        {
            "scenario": name,
            "fcf_growth_rate": by_name[name].fcf_growth_rate,
            "wacc": shared_wacc,
            "perpetual_growth": shared_perpetual_growth,
            **(by_name[name].forecast_inputs or {}),
        }
        for name in SCENARIO_NAMES
    ]


async def save_valuation_scenarios(
    db: AsyncSession,
    ticker: str,
    scenarios: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    canonical_ticker = canonicalize_ticker(ticker)
    await db.execute(
        delete(TickerValuationScenario).where(
            TickerValuationScenario.ticker == canonical_ticker
        )
    )
    db.add_all(
        TickerValuationScenario(
            ticker=canonical_ticker,
            scenario=str(scenario["scenario"]),
            fcf_growth_rate=float(scenario["fcf_growth_rate"]),
            wacc=float(scenario["wacc"]),
            perpetual_growth=float(scenario["perpetual_growth"]),
            forecast_inputs={k: scenario[k] for k in ("operating_forecast", "terminal_roic", "forecast_as_of") if k in scenario} or None,
        )
        for scenario in scenarios
    )
    await db.commit()
    saved = await get_saved_valuation_scenarios(db, canonical_ticker)
    if saved is None:  # pragma: no cover - defensive integrity guard
        raise RuntimeError("Saved valuation scenarios could not be reloaded.")
    return saved


async def delete_valuation_scenarios(db: AsyncSession, ticker: str) -> None:
    canonical_ticker = canonicalize_ticker(ticker)
    await db.execute(
        delete(TickerValuationScenario).where(
            TickerValuationScenario.ticker == canonical_ticker
        )
    )
    await db.commit()
