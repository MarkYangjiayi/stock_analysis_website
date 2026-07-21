"""Idempotently populate v2 lineage tables from legacy dashboard data.

Legacy rows remain marked as estimated and are never auto-published for
backtests because the old system did not capture filing revisions or universe
membership at observation time.
"""

import asyncio
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from database import async_session_maker, init_db
from models import FinancialStatement, FundamentalVersion, SecurityMaster, Ticker
from services.security_master import bulk_upsert_securities
from core.time_utils import utc_now


async def migrate_legacy_data() -> dict:
    await init_db()
    async with async_session_maker() as db, db.begin():
        tickers = list((await db.execute(select(Ticker))).scalars().all())
        security_records = [
            {
                "ticker": ticker.ticker,
                "name": ticker.name,
                "exchange": ticker.exchange,
                "currency": ticker.currency,
                "asset_type": None,
            }
            for ticker in tickers
        ]
        observed_date = min(
            (ticker.last_updated.date() for ticker in tickers if ticker.last_updated),
            default=utc_now().date(),
        )
        await bulk_upsert_securities(db, security_records, observed_date)

        statements = list((await db.execute(select(FinancialStatement))).scalars().all())
        version_rows = []
        for statement in statements:
            delay = 45 if statement.period == "Quarterly" else 90
            estimated_availability = datetime.combine(
                statement.fiscal_date + timedelta(days=delay), time(23, 59, 59)
            )
            version_rows.append({
                "ticker": statement.ticker,
                "period_end": statement.fiscal_date,
                "period_type": statement.period,
                "filing_at": estimated_availability,
                "available_at": estimated_availability,
                "availability_estimated": True,
                "revision": 1,
                "income_statement": statement.income_statement,
                "balance_sheet": statement.balance_sheet,
                "cash_flow": statement.cash_flow,
                "source": "legacy-import",
            })
        for start in range(0, len(version_rows), 500):
            await db.execute(
                insert(FundamentalVersion).values(version_rows[start:start + 500]).on_conflict_do_nothing(
                    index_elements=["ticker", "period_end", "period_type", "available_at", "revision"]
                )
            )
    async with async_session_maker() as db:
        security_count = len((await db.execute(select(SecurityMaster.id))).all())
        fundamental_count = len((await db.execute(select(FundamentalVersion.id))).all())
    return {
        "legacy_tickers": len(tickers),
        "security_master_rows": security_count,
        "legacy_statements": len(statements),
        "fundamental_version_rows": fundamental_count,
        "published_legacy_snapshots": 0,
    }


if __name__ == "__main__":
    print(asyncio.run(migrate_legacy_data()))
