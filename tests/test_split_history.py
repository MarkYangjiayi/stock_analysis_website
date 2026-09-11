import asyncio
from datetime import date, datetime
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import delete, select

from models import CorporateAction, DailyPrice, FundamentalVersion, RawDataSnapshot, Ticker
from services.raw_store import persist_snapshot
from services.split_history import load_split_history, parse_split_history, persist_full_split_history
from services.valuation_history import build_valuation_history, get_valuation_history
from test_valuation_history import calculate, price, statements


@pytest.mark.parametrize("factor", ["NaN", "Infinity", "1/0", "-1", "0", "n/a"])
def test_non_finite_or_invalid_split_factors_are_rejected(factor):
    with pytest.raises(ValueError):
        parse_split_history([{"date": "2024-01-01", "split": factor}])


@pytest.mark.parametrize("payload", [None, {}, [{"date": "bad", "split": "2/1"}],
    [{"date": "2024-01-01", "split": "2/1"}, {"date": "2024-01-01", "split": "3/1"}]])
def test_incomplete_and_conflicting_split_payloads_are_rejected(payload):
    with pytest.raises(ValueError):
        parse_split_history(payload)


def test_absent_coverage_disables_all_six_ratios_even_when_some_actions_exist():
    for actions in ([], [SimpleNamespace(ex_date=date(2024, 12, 2), split_factor=2)]):
        result = build_valuation_history("TEST.US", [price("2024-11-04")], statements(), actions, currency="USD")
        assert not result["split_history_verified"]
        assert all(value is None for value in result["points"][0]["values"].values())
        assert "split history" in result["points"][0]["reason"]


def test_aapl_2014_regression_requires_both_later_splits():
    # Reported quarterly inputs from the EODHD AAPL payload. Its historical
    # share counts already include both the 2014 7:1 and 2020 4:1 splits.
    rows = []
    quarters = [
        ("2013-03-31", "2013-04-24", 9547000000, 26488980000),
        ("2013-06-30", "2013-07-24", 6900000000, 25879420000),
        ("2013-09-30", "2013-10-30", 7512000000, 25455668000),
        ("2013-12-31", "2014-01-28", 13072000000, 25240656000),
    ]
    for end, filing, income, shares in quarters:
        rows.append(SimpleNamespace(
            period_end=date.fromisoformat(end), fetched_at=datetime(2026, 7, 30),
            source="EODHD", income_statement={"netIncome": income, "currency_symbol": "USD", "filing_date": filing},
            balance_sheet={"commonStockSharesOutstanding": shares, "currency_symbol": "USD"}, cash_flow={},
        ))
    splits = parse_split_history([{"date": "2014-06-09", "split": "7/1"}, {"date": "2020-08-31", "split": "4/1"}])
    actual = calculate(rows, [price("2014-04-23", 524.7508)], splits)
    assert actual["points"][0]["values"]["pe"] == pytest.approx(13.014372203183577)
    # This recreates the old missing-actions defect (364.4x), demonstrating
    # exactly why callers must provide verified coverage, not assume it.
    old = calculate(rows, [price("2014-04-23", 524.7508)], [])
    assert old["points"][0]["values"]["pe"] == pytest.approx(364.40242168914017)


@pytest.mark.parametrize("factors", [(7, 4), (4, 10)])
def test_successive_aapl_and_nvda_split_factors_do_not_create_jumps_in_any_multiple(factors):
    first, second = factors
    splits = parse_split_history([
        {"date": "2024-12-02", "split": f"{first}/1"},
        {"date": "2024-12-09", "split": f"{second}/1"},
    ])
    quotes = [price("2024-11-29", 10 * first * second), price("2024-12-02", 10 * second), price("2024-12-09", 10)]
    full = calculate(prices=quotes, splits=splits)
    expected = {"pe": 2.5, "ps": 0.25, "pb": 0.5, "pfcf": 1.25, "ev_revenue": 0.45, "ev_ebitda": 2.25}
    for point in full["points"]:
        assert point["values"] == pytest.approx(expected)
    # A truncated price window still requires splits through the share vintage.
    assert calculate(prices=quotes[:1], splits=splits)["points"][0]["values"] == pytest.approx(expected)


@pytest.mark.asyncio
async def test_partial_history_cannot_cover_legacy_prices_or_later_share_vintage(db_session):
    await persist_snapshot(db_session, "EODHD", "splits", [], details={
        "ticker": "TEST.US", "from_date": "2024-01-01", "to_date": "2025-01-01"})
    assert await load_split_history(db_session, "TEST.US", date(2014, 1, 1), date(2024, 12, 1)) is None
    assert await load_split_history(db_session, "TEST.US", date(2024, 1, 1), date(2025, 2, 1)) is None
    assert await load_split_history(db_session, "TEST.US", date(2024, 1, 1), date(2025, 1, 1)) is not None


@pytest.mark.asyncio
async def test_verified_empty_payload_is_valid_and_unchanged_refresh_advances_coverage(db_session):
    first = await persist_full_split_history(db_session, "TEST.US", [], date(2024, 12, 1))
    assert await load_split_history(db_session, "TEST.US", date(2014, 1, 1), date(2024, 12, 2)) is None
    second = await persist_full_split_history(db_session, "TEST.US", [], date(2024, 12, 2))
    assert second.id != first.id
    coverage = await load_split_history(db_session, "TEST.US", date(2014, 1, 1), date(2024, 12, 2))
    assert coverage.actions == []
    assert coverage.snapshot_id == second.id


@pytest.mark.asyncio
async def test_legacy_full_response_uses_fetch_date_not_last_split_date(db_session):
    snapshot = await persist_snapshot(db_session, "EODHD", "splits", [{"date": "2014-06-09", "split": "7/1"}],
                                      as_of_date=date(2014, 6, 9), details={"ticker": "TEST.US"})
    snapshot.fetched_at = datetime(2020, 1, 1)
    await db_session.flush()
    coverage = await load_split_history(db_session, "TEST.US", date(2013, 1, 1), date(2019, 12, 31))
    assert coverage.through_date == date(2020, 1, 1)
    assert await load_split_history(db_session, "TEST.US", date(2013, 1, 1), date(2020, 8, 31)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing", "corrupt", "invalid"])
async def test_unreadable_or_invalid_raw_snapshot_is_not_proof_of_coverage(db_session, damage):
    import gzip
    from pathlib import Path
    snapshot = await persist_full_split_history(db_session, "TEST.US", [], date(2025, 2, 1))
    path = Path(snapshot.storage_path)
    if damage == "missing":
        path.unlink()
    elif damage == "corrupt":
        path.write_bytes(b"not gzip")
    else:
        with gzip.open(path, "wt") as handle:
            handle.write('{"error":"upstream failure"}')
    assert await load_split_history(db_session, "TEST.US", date(2014, 1, 1), date(2025, 2, 1)) is None
    # A fresh observation of the same payload repairs a lost/corrupt raw file
    # without overwriting the earlier immutable observation.
    repaired = await persist_full_split_history(db_session, "TEST.US", [], date(2025, 2, 1))
    assert repaired.id != snapshot.id
    assert await load_split_history(db_session, "TEST.US", date(2014, 1, 1), date(2025, 2, 1)) is not None


async def seed_legacy_stock(db):
    db.add(Ticker(ticker="TEST.US", currency="USD", sector="Technology"))
    await db.flush()
    db.add(DailyPrice(ticker="TEST.US", date=date(2024, 11, 4), close=40, adjusted_close=3))
    for row in statements():
        values = vars(row).copy()
        values.pop("raw_snapshot_id")
        db.add(FundamentalVersion(ticker="TEST.US", period_type="Quarterly", **values))
    await db.commit()


@pytest.mark.asyncio
async def test_read_only_does_not_fetch_and_concurrent_stock_reads_repair_once(db_session, monkeypatch):
    from main import app
    await seed_legacy_stock(db_session)
    calls, limits = [], []
    async def splits(ticker, **kwargs):
        calls.append((ticker, kwargs))
        await asyncio.sleep(0.01)
        return [{"date": "2024-12-02", "split": "2/1"}]
    async def fresh(*args, **kwargs):
        return SimpleNamespace(needs_sync=False)
    async def limit(request):
        limits.append(request)
    monkeypatch.setattr("services.split_history.eodhd_client.get_splits", splits)
    monkeypatch.setattr("api.routers.assess_ticker_freshness", fresh)
    monkeypatch.setattr("api.routers.limit_expensive_requests", limit)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        before = (await client.get("/api/stocks/TEST/valuation-history")).json()
        assert before["points"][0]["values"]["pe"] is None
        assert not calls
        responses = await asyncio.gather(client.get("/api/stocks/TEST"), client.get("/api/stocks/TEST"))
        for response in responses:
            assert response.status_code == 200
            history = response.json()["valuation_history"]
            assert history["split_history_verified"]
            assert history["split_history_snapshot_id"]
            assert history["points"][0]["values"] == pytest.approx({
                "pe": 5, "ps": 0.5, "pb": 1, "pfcf": 2.5, "ev_revenue": 0.7, "ev_ebitda": 3.5})
        assert len(calls) == len(limits) == 1
        assert "from_date" not in calls[0][1]
    # Calculation uses the verified raw response even if normalized action
    # rows are missing. Their existence alone never proves completeness.
    await db_session.rollback()
    await db_session.execute(delete(CorporateAction))
    await db_session.commit()
    assert (await get_valuation_history("TEST.US", db_session))["points"][0]["values"]["pe"] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["upstream", "invalid", "rate_limit"])
async def test_failed_optional_repair_keeps_price_page_but_suppresses_ratios(db_session, monkeypatch, failure):
    from fastapi import HTTPException
    from main import app
    await seed_legacy_stock(db_session)
    async def splits(*args, **kwargs):
        if failure == "rate_limit":
            pytest.fail("A rate-limited request must not call the provider")
        return None if failure == "upstream" else [{"date": "2024-12-02", "split": "NaN"}]
    async def fresh(*args, **kwargs):
        return SimpleNamespace(needs_sync=False)
    async def limit(request):
        if failure == "rate_limit":
            raise HTTPException(429, "rate limited")
    monkeypatch.setattr("services.split_history.eodhd_client.get_splits", splits)
    monkeypatch.setattr("api.routers.assess_ticker_freshness", fresh)
    monkeypatch.setattr("api.routers.limit_expensive_requests", limit)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/stocks/TEST")
    assert response.status_code == 200
    assert response.json()["historical_data"]
    history = response.json()["valuation_history"]
    assert not history["split_history_verified"]
    assert all(value is None for value in history["points"][0]["values"].values())
    assert not (await db_session.execute(select(RawDataSnapshot))).scalars().all()
