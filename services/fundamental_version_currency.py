"""Repair legacy statement currencies from their own immutable raw payloads.

Older normalizers copied statement rows without inheriting the section-level
currency declared by EODHD.  The raw snapshots retained that declaration, so
we can restore the omitted metadata without guessing from today's quote or
creating a later-dated economic revision.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func, or_, select

from database import async_session_maker
from models import FundamentalVersion, RawDataSnapshot


logger = logging.getLogger(__name__)

_SECTIONS = (
    ("income_statement", "Income_Statement"),
    ("balance_sheet", "Balance_Sheet"),
    ("cash_flow", "Cash_Flow"),
)


def _read_payload(path: str) -> dict[str, Any]:
    source = Path(path)
    opener = gzip.open if source.suffix == ".gz" else open
    with opener(source, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def _without_currency(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "currency_symbol"}


def enrich_statement_currency(
    statement: Any,
    section: Any,
    period_key: str,
    period_end: str,
) -> tuple[Any, bool]:
    """Add only a currency proven by the matching source statement row."""
    if not isinstance(statement, dict) or not statement:
        return statement, False
    if str(statement.get("currency_symbol") or "").strip():
        return statement, False
    if not isinstance(section, dict):
        return statement, False
    period_rows = section.get(period_key)
    raw_statement = period_rows.get(period_end) if isinstance(period_rows, dict) else None
    if not isinstance(raw_statement, dict):
        return statement, False
    if _without_currency(statement) != _without_currency(raw_statement):
        return statement, False
    currency = raw_statement.get("currency_symbol") or section.get("currency_symbol")
    currency = str(currency or "").strip().upper()
    if not currency:
        return statement, False
    return {**statement, "currency_symbol": currency}, True


def _missing_statement_currency(column):
    return and_(
        func.json_type(column) == "object",
        func.json(column) != "{}",
        func.coalesce(func.json_extract(column, "$.currency_symbol"), "") == "",
    )


def _missing_currency_clause():
    return or_(
        _missing_statement_currency(FundamentalVersion.income_statement),
        _missing_statement_currency(FundamentalVersion.balance_sheet),
        _missing_statement_currency(FundamentalVersion.cash_flow),
    )


async def _load_payload_batch(
    snapshots: list[RawDataSnapshot],
) -> tuple[dict[int, dict[str, Any]], int]:
    semaphore = asyncio.Semaphore(8)

    async def load(snapshot: RawDataSnapshot) -> tuple[int, dict[str, Any] | None]:
        async with semaphore:
            try:
                return snapshot.id, await asyncio.to_thread(
                    _read_payload, snapshot.storage_path
                )
            except (OSError, EOFError, TypeError, ValueError, json.JSONDecodeError):
                logger.warning(
                    "Unable to read fundamentals snapshot %s while repairing currencies",
                    snapshot.id,
                )
                return snapshot.id, None

    loaded = await asyncio.gather(*(load(snapshot) for snapshot in snapshots))
    return (
        {snapshot_id: payload for snapshot_id, payload in loaded if payload is not None},
        sum(payload is None for _, payload in loaded),
    )


async def repair_fundamental_version_currencies(
    *, batch_size: int = 50
) -> dict[str, int]:
    """Restore omitted currencies in bounded, resumable transactions.

    A field is changed only when the version's own ``raw_snapshot_id`` points
    to an EODHD fundamentals payload whose same-period statement matches after
    removing ``currency_symbol``.  This preserves point-in-time availability
    and never infers currency from a current ticker profile.
    """
    batch_size = max(1, batch_size)
    missing = _missing_currency_clause()
    async with async_session_maker() as db:
        snapshot_ids = list(
            (
                await db.execute(
                    select(FundamentalVersion.raw_snapshot_id)
                    .where(
                        FundamentalVersion.raw_snapshot_id.is_not(None),
                        missing,
                    )
                    .distinct()
                    .order_by(FundamentalVersion.raw_snapshot_id)
                )
            ).scalars()
        )

    stats = {
        "candidate_snapshots": len(snapshot_ids),
        "snapshots_processed": 0,
        "snapshots_unreadable": 0,
        "versions_examined": 0,
        "versions_repaired": 0,
        "statement_fields_repaired": 0,
        "versions_unmatched": 0,
    }
    for start in range(0, len(snapshot_ids), batch_size):
        batch = snapshot_ids[start : start + batch_size]
        async with async_session_maker() as db:
            snapshots = list(
                (
                    await db.execute(
                        select(RawDataSnapshot).where(
                            RawDataSnapshot.id.in_(batch),
                            RawDataSnapshot.source == "EODHD",
                            RawDataSnapshot.dataset == "fundamentals",
                            RawDataSnapshot.status == "stored",
                        )
                    )
                ).scalars()
            )
            payloads, unreadable = await _load_payload_batch(snapshots)
            stats["snapshots_unreadable"] += unreadable + len(batch) - len(snapshots)
            snapshot_by_id = {snapshot.id: snapshot for snapshot in snapshots}
            versions = list(
                (
                    await db.execute(
                        select(FundamentalVersion).where(
                            FundamentalVersion.raw_snapshot_id.in_(batch),
                            missing,
                        )
                    )
                ).scalars()
            )
            for version in versions:
                stats["versions_examined"] += 1
                snapshot = snapshot_by_id.get(version.raw_snapshot_id)
                payload = payloads.get(version.raw_snapshot_id)
                details = snapshot.details if snapshot is not None else None
                if (
                    payload is None
                    or not isinstance(details, dict)
                    or str(details.get("ticker") or "").upper()
                    != version.ticker.upper()
                ):
                    stats["versions_unmatched"] += 1
                    continue
                financials = payload.get("Financials")
                if not isinstance(financials, dict):
                    stats["versions_unmatched"] += 1
                    continue
                period_key = (
                    "quarterly"
                    if version.period_type == "Quarterly"
                    else "yearly"
                    if version.period_type == "Yearly"
                    else ""
                )
                if not period_key:
                    stats["versions_unmatched"] += 1
                    continue
                changed = 0
                for attribute, section_name in _SECTIONS:
                    enriched, repaired = enrich_statement_currency(
                        getattr(version, attribute),
                        financials.get(section_name),
                        period_key,
                        version.period_end.isoformat(),
                    )
                    if repaired:
                        setattr(version, attribute, enriched)
                        changed += 1
                if changed:
                    stats["versions_repaired"] += 1
                    stats["statement_fields_repaired"] += changed
                else:
                    stats["versions_unmatched"] += 1
            await db.commit()
        stats["snapshots_processed"] += len(batch)
        if stats["snapshots_processed"] % 500 == 0 or stats["snapshots_processed"] == len(snapshot_ids):
            logger.info(
                "Fundamental currency repair: %d/%d snapshots, %d versions repaired",
                stats["snapshots_processed"],
                len(snapshot_ids),
                stats["versions_repaired"],
            )
    return stats
