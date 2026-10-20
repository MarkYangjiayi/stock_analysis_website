"""Fill consensus-revision and short-interest fields into the screener cache.

The screener refetches each ticker's fundamentals at least weekly, so these
fields arrive on their own within about a week of deploying. This script
derives them now from the payloads already stored under ``data/raw`` (no
provider calls), so the next screener publication carries them for every
cached ticker. Published snapshots are not modified. Safe to re-run: rows that
already have the fields are skipped unless ``--force`` is given.

Examples:
    python scripts/rederive_expectation_signals.py --dry-run
    python scripts/rederive_expectation_signals.py
"""

import argparse
import asyncio
import gzip
import json
import logging
import os
import sys
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from database import async_session_maker
from models import RawDataSnapshot, ScreenerFundamentalCache
from services.expectation_signals import SCREENER_FIELDS, screener_values
from services.screener_normalization import normalize_public_screener_values

logger = logging.getLogger("rederive_expectation_signals")
BATCH_SIZE = 200


def _read_payload(path: str) -> dict:
    file_path = Path(path)
    opener = gzip.open if file_path.suffix == ".gz" else open
    with opener(file_path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


async def rederive(
    *,
    force: bool = False,
    dry_run: bool = False,
    fields: tuple[str, ...] = SCREENER_FIELDS,
    derive=screener_values,
) -> dict[str, int]:
    """Derive ``fields`` with ``derive(payload)``; the quality scorecard reuses this."""
    counts = {"cached": 0, "updated": 0, "already_present": 0, "no_raw_snapshot": 0, "unreadable": 0}
    async with async_session_maker() as db:
        rows = (await db.execute(
            select(ScreenerFundamentalCache, RawDataSnapshot.storage_path)
            .outerjoin(RawDataSnapshot, RawDataSnapshot.id == ScreenerFundamentalCache.raw_snapshot_id)
            .order_by(ScreenerFundamentalCache.ticker)
        )).all()
    counts["cached"] = len(rows)
    pending = []
    for cache_row, storage_path in rows:
        data = dict(cache_row.normalized_data or {})
        if not force and all(field in data for field in fields):
            counts["already_present"] += 1
            continue
        if not storage_path:
            counts["no_raw_snapshot"] += 1
            continue
        try:
            payload = await asyncio.to_thread(_read_payload, storage_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Skipping %s: %s", cache_row.ticker, type(exc).__name__)
            counts["unreadable"] += 1
            continue
        data.update(normalize_public_screener_values(derive(payload)))
        pending.append((cache_row.ticker, data))

    if not dry_run:
        for start in range(0, len(pending), BATCH_SIZE):
            async with async_session_maker() as db, db.begin():
                for ticker, data in pending[start:start + BATCH_SIZE]:
                    cache_row = await db.get(ScreenerFundamentalCache, ticker)
                    if cache_row is not None:
                        # Keep as_of_date and fetched_at: the payload is unchanged.
                        cache_row.normalized_data = data
            logger.info("Updated %s / %s cache rows", min(start + BATCH_SIZE, len(pending)), len(pending))
    counts["updated"] = len(pending)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="Recompute rows that already have the fields.")
    parser.add_argument("--dry-run", action="store_true", help="Count rows without writing.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    counts = asyncio.run(rederive(force=args.force, dry_run=args.dry_run))
    verb = "would update" if args.dry_run else "updated"
    print(
        f"{counts['cached']} cached tickers: {verb} {counts['updated']}, "
        f"{counts['already_present']} already had the fields, "
        f"{counts['no_raw_snapshot']} without a stored payload, {counts['unreadable']} unreadable."
    )


if __name__ == "__main__":
    main()
