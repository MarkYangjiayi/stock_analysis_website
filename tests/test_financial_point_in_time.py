from datetime import date

import pytest
from sqlalchemy import select

from models import FundamentalVersion, Ticker
from services.corporate_actions import upsert_corporate_actions
from services.data_sync import _upsert_financials
from models import CorporateAction


def fundamental_payload(revenue: int):
    return {
        "Financials": {
            "Income_Statement": {"quarterly": {"2025-03-31": {"totalRevenue": revenue, "netIncome": 10}}},
            "Balance_Sheet": {"quarterly": {"2025-03-31": {"totalAssets": 100}}},
            "Cash_Flow": {"quarterly": {"2025-03-31": {"freeCashFlow": 8}}},
        }
    }


@pytest.mark.asyncio
async def test_fundamental_revisions_are_preserved(db_session):
    db_session.add(Ticker(ticker="AAA.US"))
    await db_session.flush()
    await _upsert_financials("AAA.US", fundamental_payload(100), db_session)
    await db_session.commit()
    await _upsert_financials("AAA.US", fundamental_payload(100), db_session)
    await db_session.commit()
    first_count = len((await db_session.execute(select(FundamentalVersion))).scalars().all())
    assert first_count == 1

    await _upsert_financials("AAA.US", fundamental_payload(120), db_session)
    await db_session.commit()
    versions = (await db_session.execute(select(FundamentalVersion).order_by(FundamentalVersion.revision))).scalars().all()
    assert [version.revision for version in versions] == [1, 2]
    assert versions[1].available_at >= versions[0].available_at


@pytest.mark.asyncio
async def test_corporate_actions_are_upserted(db_session):
    count = await upsert_corporate_actions(
        db_session,
        "AAA.US",
        [{"date": "2025-01-02", "split": "2/1"}],
        [{"date": "2025-02-03", "value": "0.25", "currency": "USD"}],
    )
    await db_session.commit()
    rows = (await db_session.execute(select(CorporateAction).order_by(CorporateAction.ex_date))).scalars().all()
    assert count == 2
    assert float(rows[0].split_factor) == 2.0
    assert float(rows[1].cash_amount) == 0.25
