"""Read-only validation for the latest published screener snapshot."""

import argparse
import asyncio
import json
import math
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from database import async_session_maker
from models import StockScreenerSnapshot
from services.screener_fields import FIELD_DEFINITIONS
from services.screener_query import get_screener_metadata, query_screener


def is_non_finite_numeric(value: object) -> bool:
    if isinstance(value, Decimal):
        return not value.is_finite()
    return isinstance(value, float) and not math.isfinite(value)


async def validate(min_core_coverage: float) -> dict:
    async with async_session_maker() as db:
        metadata = await get_screener_metadata(db)
        as_of_date = metadata["as_of_date"]
        if not as_of_date:
            raise RuntimeError("no published screener snapshot")
        core = ("market_cap", "close", "volume")
        coverage = {field["id"]: field["coverage"] for field in metadata["fields"]}
        failures = [
            f"{field_name} coverage {coverage.get(field_name, 0):.1%}"
            for field_name in core
            if coverage.get(field_name, 0) < min_core_coverage
        ]
        selected_date = date.fromisoformat(as_of_date)
        result = await db.execute(
            select(StockScreenerSnapshot).where(
                StockScreenerSnapshot.date == selected_date
            )
        )
        invalid = {}
        for row in result.scalars().all():
            for definition in FIELD_DEFINITIONS:
                if not definition.column or not hasattr(row, definition.column):
                    continue
                value = getattr(row, definition.column)
                if is_non_finite_numeric(value):
                    invalid[definition.id] = invalid.get(definition.id, 0) + 1
        if invalid:
            failures.append("non-finite values: " + ", ".join(sorted(invalid)))

        samples = {}
        sample_filters = {
            "large_cap": [{"field": "market_cap", "operator": "gte", "value": 10_000_000_000}],
            "profitable_growth": [
                {"field": "pe_ratio", "operator": "gt", "value": 0},
                {"field": "roe", "operator": "gte", "value": 0.15},
                {"field": "sales_growth_5yr", "operator": "gte", "value": 0.10},
            ],
            "oversold": [{"field": "rsi_14", "operator": "lt", "value": 30}],
        }
        for name, filters in sample_filters.items():
            query = await query_screener({
                "filters": filters,
                "columns": ["market_cap", "pe_ratio", "roe", "sales_growth_5yr", "rsi_14"],
                "limit": 5,
                "offset": 0,
            }, db)
            samples[name] = {"total": query["total"], "tickers": [item["ticker"] for item in query["items"]]}

        return {
            "passed": not failures,
            "as_of_date": as_of_date,
            "freshness": metadata["freshness"],
            "record_count": metadata["record_count"],
            "supported_finviz_fields": metadata["supported_finviz_fields"],
            "coverage": coverage,
            "invalid_numeric_values": invalid,
            "sample_queries": samples,
            "failures": failures,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the latest screener snapshot without writing data.")
    parser.add_argument("--min-core-coverage", type=float, default=0.80)
    args = parser.parse_args()
    report = asyncio.run(validate(args.min_core_coverage))
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
