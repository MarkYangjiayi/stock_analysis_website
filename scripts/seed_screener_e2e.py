"""Create a deterministic screener database for browser tests."""

import asyncio
from datetime import date

from core.config import settings
from database import async_session_maker, engine
from models import Base, DataPublication, PipelineRun, StockScreenerSnapshot, UniverseMembership


def assert_safe_e2e_database(environment: str, database_url: str) -> None:
    allowed_database_url = "sqlite+aiosqlite:///./data/screener_e2e.db"
    if environment.lower() != "test" or database_url != allowed_database_url:
        raise RuntimeError(
            "refusing to reset a database that is not explicitly marked for E2E tests"
        )


async def seed() -> None:
    assert_safe_e2e_database(settings.ENVIRONMENT, settings.DATABASE_URL)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    as_of = date(2025, 1, 2)
    async with async_session_maker() as db, db.begin():
        run = PipelineRun(
            pipeline_name="daily_screener",
            target_date=as_of,
            status="published",
            stage="published",
        )
        db.add(run)
        await db.flush()
        db.add(DataPublication(
            dataset="screener",
            as_of_date=as_of,
            pipeline_run_id=run.id,
            status="published",
        ))
        for index in range(120):
            ticker = f"T{index:03d}.US"
            sector = "Technology" if index % 2 == 0 else "Healthcare"
            peg_ratio_raw = -0.5 if index == 0 else 0 if index == 1 else 0.4 + index / 10
            db.add(StockScreenerSnapshot(
                ticker=ticker,
                name=f"Fixture Company {index:03d}",
                date=as_of,
                exchange="NASDAQ" if index % 3 else "NYSE",
                sector=sector,
                industry="Software - Application" if sector == "Technology" else "Biotechnology",
                country="USA",
                market_cap=500_000_000 + index * 1_000_000_000,
                pe_ratio=8 + index / 2,
                peg_ratio_raw=peg_ratio_raw,
                peg_ratio=peg_ratio_raw if peg_ratio_raw > 0 else None,
                pb_ratio=1 + index / 20,
                dividend_yield=0.01 + (index % 5) / 100,
                roe=0.05 + index / 1000,
                debt_to_equity=0.2 + index / 100,
                gross_margin=0.25 + index / 1000,
                sales_growth_5yr=0.02 + index / 1000,
                fcf=10_000_000 + index * 1_000_000,
                technical_quality="ok",
                close=20 + index,
                volume=100_000 + index * 10_000,
                ma20=19 + index,
                ma50=18 + index,
                rsi_14=20 + index / 2,
            ))
            db.add(UniverseMembership(
                universe="SP500" if index < 60 else "RUSSELL2000",
                ticker=ticker,
                effective_from=as_of,
                source_run_id=run.id,
            ))


if __name__ == "__main__":
    asyncio.run(seed())
