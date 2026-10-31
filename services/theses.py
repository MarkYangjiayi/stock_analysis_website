"""Versioned investment theses for core-tier tickers.

Every save appends a version and the highest version is the current thesis.
A save names the version it was edited from, so an editor left open on an old
version cannot overwrite a thesis saved since from another tab or session.
"""
from __future__ import annotations

from datetime import timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models import TickerThesisVersion
from services.security_master import canonicalize_ticker

THESIS_HISTORY_LIMIT = 50


class ThesisConflict(Exception):
    """The thesis was saved again after the editor loaded it."""

    def __init__(self, current_version: int):
        super().__init__(f"The thesis is now at version {current_version}.")
        self.current_version = current_version


def _serialize(row: TickerThesisVersion) -> dict[str, Any]:
    return {
        "version": row.version,
        "content": row.content,
        "note": row.note,
        # Stored as naive UTC; say so, or browsers read it as local time.
        "created_at": row.created_at.replace(tzinfo=timezone.utc).isoformat(),
    }


async def get_thesis(db: AsyncSession, ticker: str) -> dict[str, Any] | None:
    row = await db.scalar(
        select(TickerThesisVersion)
        .where(TickerThesisVersion.ticker == canonicalize_ticker(ticker))
        .order_by(TickerThesisVersion.version.desc())
        .limit(1)
    )
    return _serialize(row) if row else None


async def list_thesis_versions(
    db: AsyncSession,
    ticker: str,
    limit: int = THESIS_HISTORY_LIMIT,
) -> list[dict[str, Any]]:
    """Newest first."""
    result = await db.execute(
        select(TickerThesisVersion)
        .where(TickerThesisVersion.ticker == canonicalize_ticker(ticker))
        .order_by(TickerThesisVersion.version.desc())
        .limit(limit)
    )
    return [_serialize(row) for row in result.scalars().all()]


async def save_thesis(
    db: AsyncSession,
    ticker: str,
    content: dict[str, Any],
    base_version: int,
    note: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Save ``content`` as the next version if the thesis is still at
    ``base_version`` (0 when none exists). Returns the current thesis and
    whether a version was added; unchanged content adds none."""
    canonical_ticker = canonicalize_ticker(ticker)
    current = await get_thesis(db, canonical_ticker)
    current_version = current["version"] if current else 0
    if base_version != current_version:
        raise ThesisConflict(current_version)
    if current is not None and current["content"] == content:
        return current, False
    db.add(TickerThesisVersion(
        ticker=canonical_ticker,
        version=current_version + 1,
        content=content,
        note=note or None,
    ))
    try:
        await db.commit()
    except IntegrityError:
        # A concurrent save took this version number first.
        await db.rollback()
        latest = await get_thesis(db, canonical_ticker)
        raise ThesisConflict(latest["version"] if latest else 0) from None
    saved = await get_thesis(db, canonical_ticker)
    if saved is None:  # pragma: no cover - defensive integrity guard
        raise RuntimeError("Saved thesis could not be reloaded.")
    return saved, True
