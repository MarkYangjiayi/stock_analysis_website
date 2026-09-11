"""Verified split coverage for historical price/share normalization.

An empty corporate-actions table is not evidence that a stock never split.
Only a successfully stored provider response covering the required date range
can establish that, including a verified empty response for no-split stocks.
"""
import asyncio
import gzip
import json
import logging
from dataclasses import dataclass
from datetime import date
from decimal import DecimalException
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.time_utils import utc_now
from models import RawDataSnapshot
from services import eodhd_client
from services.corporate_actions import _parse_date, _parse_split_factor, upsert_corporate_actions
from services.raw_store import persist_snapshot

logger = logging.getLogger(__name__)
UNVERIFIED_REASON = "Complete split history has not been verified; historical multiples are unavailable until it is refreshed."


@dataclass(frozen=True)
class SplitHistory:
    actions: list
    snapshot_id: int
    through_date: date


def parse_split_history(payload: object) -> list:
    if not isinstance(payload, list):
        raise ValueError("Split history must be a complete list response.")
    factors = {}
    for item in payload:
        try:
            day = _parse_date(item["date"])
            factor = _parse_split_factor(item.get("split") or item.get("split_factor"))
            if factor is None or not factor.is_finite() or factor <= 0:
                raise ValueError("Invalid split factor.")
        except (KeyError, AttributeError, TypeError, ValueError, DecimalException) as exc:
            raise ValueError("Invalid split history row.") from exc
        if day in factors and factors[day] != factor:
            raise ValueError("Conflicting split history rows.")
        factors[day] = factor
    return [SimpleNamespace(ex_date=day, split_factor=factor) for day, factor in sorted(factors.items())]


def _read_actions(path: str) -> list:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return parse_split_history(json.load(handle))


async def load_split_history(db: AsyncSession, ticker: str, start: date, end: date) -> SplitHistory | None:
    snapshots = (await db.execute(select(RawDataSnapshot).where(
        RawDataSnapshot.source == "EODHD", RawDataSnapshot.dataset == "splits",
        RawDataSnapshot.status == "stored", RawDataSnapshot.details["ticker"].as_string() == ticker,
    ).order_by(RawDataSnapshot.fetched_at.desc(), RawDataSnapshot.id.desc()))).scalars()
    for snapshot in snapshots:
        details = snapshot.details or {}
        try:
            # Legacy unbounded syncs had no date filters. Their fetch date, not
            # the last split's ex-date, is the observation horizon.
            first = date.fromisoformat(details["from_date"]) if details.get("from_date") else date.min
            through = date.fromisoformat(details["to_date"]) if details.get("to_date") else snapshot.fetched_at.date()
            if first > start or through < end:
                continue
            actions = await asyncio.to_thread(_read_actions, snapshot.storage_path)
            if any(action.ex_date < first or action.ex_date > through for action in actions):
                continue
            return SplitHistory(actions, snapshot.id, through)
        except (OSError, EOFError, TypeError, ValueError):
            logger.warning("Unreadable or invalid split snapshot %s for %s", snapshot.id, ticker)
    return None


async def persist_full_split_history(db: AsyncSession, ticker: str, payload: list, through: date) -> RawDataSnapshot:
    actions = parse_split_history(payload)
    if any(action.ex_date > through for action in actions):
        raise ValueError("Split response exceeds its requested coverage.")
    snapshot = await persist_snapshot(
        db, "EODHD", "splits", payload, as_of_date=through,
        # Include the horizon in immutable identity: unchanged split payloads
        # must still advance coverage on a later successful refresh.
        details={"ticker": ticker, "from_date": None, "to_date": through.isoformat(), "coverage": "full_history",
                 "observed_at": utc_now().isoformat()},
    )
    await upsert_corporate_actions(db, ticker, payload, [])
    return snapshot


async def sync_full_split_history(db: AsyncSession, ticker: str) -> bool:
    through = utc_now().date()
    try:
        payload = await eodhd_client.get_splits(ticker, to_date=through.isoformat())
        await persist_full_split_history(db, ticker, payload, through)
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        logger.exception("Unable to refresh complete split history for %s", ticker)
        return False
