from datetime import date, datetime

import pytest
from sqlalchemy import select

from models import FundamentalVersion, Ticker
from services.fundamental_version_currency import (
    enrich_statement_currency,
    repair_fundamental_version_currencies,
)
from services.raw_store import persist_snapshot


def _payload():
    return {
        "Financials": {
            "Income_Statement": {
                "currency_symbol": "usd",
                "quarterly": {"2025-03-31": {"date": "2025-03-31", "netIncome": 10}},
            },
            "Balance_Sheet": {
                "currency_symbol": "USD",
                "quarterly": {"2025-03-31": {"date": "2025-03-31", "totalAssets": 100}},
            },
            "Cash_Flow": {
                "currency_symbol": "USD",
                "quarterly": {"2025-03-31": {"date": "2025-03-31", "operatingCashFlow": 8}},
            },
        }
    }


def test_currency_enrichment_requires_the_exact_source_statement():
    section = _payload()["Financials"]["Income_Statement"]
    statement, repaired = enrich_statement_currency(
        {"date": "2025-03-31", "netIncome": 10},
        section,
        "quarterly",
        "2025-03-31",
    )
    assert repaired is True
    assert statement["currency_symbol"] == "USD"

    unchanged, repaired = enrich_statement_currency(
        {"date": "2025-03-31", "netIncome": 11},
        section,
        "quarterly",
        "2025-03-31",
    )
    assert repaired is False
    assert "currency_symbol" not in unchanged


@pytest.mark.asyncio
async def test_legacy_versions_recover_currency_from_their_own_raw_snapshot(db_session):
    db_session.add(Ticker(ticker="AAA.US"))
    snapshot = await persist_snapshot(
        db_session,
        "EODHD",
        "fundamentals",
        _payload(),
        details={"ticker": "AAA.US"},
    )
    await db_session.flush()
    db_session.add(
        FundamentalVersion(
            ticker="AAA.US",
            period_end=date(2025, 3, 31),
            period_type="Quarterly",
            filing_at=datetime(2025, 5, 1),
            available_at=datetime(2025, 5, 1),
            availability_estimated=False,
            revision=1,
            income_statement={"date": "2025-03-31", "netIncome": 10},
            balance_sheet={"date": "2025-03-31", "totalAssets": 100},
            cash_flow={"date": "2025-03-31", "operatingCashFlow": 8},
            source="EODHD",
            raw_snapshot_id=snapshot.id,
        )
    )
    await db_session.commit()

    stats = await repair_fundamental_version_currencies(batch_size=1)
    assert stats["versions_repaired"] == 1
    assert stats["statement_fields_repaired"] == 3

    db_session.expire_all()
    row = (await db_session.execute(select(FundamentalVersion))).scalar_one()
    assert row.income_statement["currency_symbol"] == "USD"
    assert row.balance_sheet["currency_symbol"] == "USD"
    assert row.cash_flow["currency_symbol"] == "USD"

    repeated = await repair_fundamental_version_currencies(batch_size=1)
    assert repeated["candidate_snapshots"] == 0


@pytest.mark.asyncio
async def test_empty_statement_objects_do_not_keep_the_repair_pending(db_session):
    db_session.add(Ticker(ticker="EMPTY.US"))
    snapshot = await persist_snapshot(
        db_session,
        "EODHD",
        "fundamentals",
        {"Financials": {}},
        details={"ticker": "EMPTY.US"},
    )
    await db_session.flush()
    db_session.add(
        FundamentalVersion(
            ticker="EMPTY.US",
            period_end=date(2025, 3, 31),
            period_type="Quarterly",
            filing_at=datetime(2025, 5, 1),
            available_at=datetime(2025, 5, 1),
            availability_estimated=False,
            revision=1,
            income_statement={},
            balance_sheet=None,
            cash_flow=None,
            source="EODHD",
            raw_snapshot_id=snapshot.id,
        )
    )
    await db_session.commit()

    stats = await repair_fundamental_version_currencies(batch_size=1)
    assert stats["candidate_snapshots"] == 0
