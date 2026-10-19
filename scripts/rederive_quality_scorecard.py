"""Fill the quality-scorecard fields into the screener cache.

Same approach as rederive_expectation_signals.py: the fields are derived from
the fundamentals payloads already stored under ``data/raw`` (no provider
calls), so the next screener publication carries them for every cached ticker
instead of waiting up to a week for each ticker's refresh. Published snapshots
are not modified. Safe to re-run: rows that already have the fields are
skipped unless ``--force`` is given.

Examples:
    python scripts/rederive_quality_scorecard.py --dry-run
    python scripts/rederive_quality_scorecard.py
"""

import argparse
import asyncio
import logging
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from rederive_expectation_signals import rederive
from services.quality_scorecard import SCREENER_FIELDS, screener_values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="Recompute rows that already have the fields.")
    parser.add_argument("--dry-run", action="store_true", help="Count rows without writing.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    counts = asyncio.run(rederive(force=args.force, dry_run=args.dry_run, fields=SCREENER_FIELDS, derive=screener_values))
    verb = "would update" if args.dry_run else "updated"
    print(
        f"{counts['cached']} cached tickers: {verb} {counts['updated']}, "
        f"{counts['already_present']} already had the fields, "
        f"{counts['no_raw_snapshot']} without a stored payload, {counts['unreadable']} unreadable."
    )


if __name__ == "__main__":
    main()
