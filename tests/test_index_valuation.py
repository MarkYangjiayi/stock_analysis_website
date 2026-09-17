"""Index-level valuation: pure aggregation, publication pipeline, and serving."""

import gzip
import json
import os
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import func, select

from api.schemas import IndexValuationResponse
from core.config import settings
from models import (
    DataPublication,
    DailyPrice,
    FundamentalVersion,
    IndexValuationSnapshot,
    PipelineRun,
    Ticker,
    UniverseMembership,
)
from services.index_valuation import (
    INDEX_VALUATION_DATASET,
    IndexValuationUnavailable,
    IndexValuationUniverseUnavailable,
    build_index_valuation_rows,
    company_keys,
    completed_month_end_labels,
    get_index_valuation,
    load_cik_map,
    refresh_index_valuation,
    validate_index_valuation_rows,
)
from services.split_history import persist_full_split_history
from services.universe import HISTORICAL_UNIVERSE_DATASET, HISTORICAL_UNIVERSE_SOURCE
from scripts.backfill_index_valuation import statement_window_complete


def _quarters(first: date, count: int) -> list[date]:
    return [first + timedelta(days=91 * index) for index in range(count)]


def test_statement_window_complete_requires_head_tail_and_continuity():
    start, end = date(2024, 1, 1), date(2026, 6, 30)
    full = _quarters(date(2023, 1, 5), 14)
    assert statement_window_complete(full, None, start, end) is True

    # Enough rows but concentrated at the recent tail: the head is missing and
    # the early months would never self-heal, so this is not completion.
    recent_only = full[4:]
    assert statement_window_complete(recent_only, None, start, end) is False

    # A quarter-sized hole breaks the four-consecutive-quarter requirement.
    with_hole = [quarter for quarter in full if quarter != full[7]]
    assert statement_window_complete(with_hole, None, start, end) is False

    # Nothing near the window end: stale history, not completion.
    assert statement_window_complete(full[:12], None, start, end) is False

    # The same recent-only set is complete when the member listed that late:
    # its own first price bounds the expected span.
    assert statement_window_complete(recent_only, date(2024, 6, 1), start, end) is True


def _point(price_date: str, equity, earnings_ttm, earnings_reason=None):
    """A reconstructed month point; the dict key carries the month-end label."""
    return {
        "price_date": price_date,
        "equity": equity,
        "earnings_ttm": earnings_ttm,
        "earnings_reason": earnings_reason,
    }


def test_company_keys_static_pairs_take_precedence_over_partial_cik_maps(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings, "INDEX_VALUATION_SEC_TICKERS_PATH", str(tmp_path / "missing.json.gz")
    )
    # The current SEC file lists only the surviving class of a delisted pair
    # (CMCSA yes, CMCSK no); the declared pair must still merge.
    one_sided = company_keys(["CMCSA.US", "CMCSK.US"], {"CMCSA": "1166691"})
    assert one_sided["CMCSA.US"] == one_sided["CMCSK.US"] == "STATIC:CMCSA"

    # Declared pairs stay merged even when the SEC file knows both classes.
    both_listed = company_keys(
        ["GOOG.US", "GOOGL.US"], {"GOOG": "1652044", "GOOGL": "1652044"}
    )
    assert both_listed["GOOG.US"] == both_listed["GOOGL.US"] == "STATIC:GOOG"

    # Pairs the static list does not know still group through the CIK map.
    undeclared = company_keys(["NEWA.US", "NEWC.US"], {"NEWA": "999", "NEWC": "999"})
    assert undeclared["NEWA.US"] == undeclared["NEWC.US"] == "CIK:999"

    assert company_keys(["AAPL.US"], {})["AAPL.US"] == "TICKER:AAPL"


def test_load_cik_map_reads_gzip_and_tolerates_missing_file(tmp_path):
    payload = {
        "0": {"cik_str": 1652044, "ticker": "GOOG", "title": "Alphabet Inc."},
        "1": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
    }
    path = tmp_path / "company_tickers.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)
    assert load_cik_map(str(path)) == {"GOOG": "1652044", "GOOGL": "1652044"}
    assert load_cik_map(str(tmp_path / "absent.json.gz")) == {}


def test_completed_month_end_labels_exclude_running_month():
    # Mid-month targets never emit the running month's (future) month-end label.
    assert completed_month_end_labels(date(2009, 11, 15), date(2010, 2, 3)) == [
        date(2009, 11, 30), date(2009, 12, 31), date(2010, 1, 31),
    ]
    # A month whose final session (Friday the 28th; the 31st is Memorial Day)
    # has been observed is complete, even though the label is the calendar 31st.
    assert completed_month_end_labels(date(2010, 5, 1), date(2010, 5, 28)) == [
        date(2010, 5, 31),
    ]
    assert completed_month_end_labels(date(2010, 5, 1), date(2010, 5, 27)) == []
    # A Saturday target after the month's final Friday session includes July.
    assert completed_month_end_labels(date(2010, 7, 1), date(2010, 7, 31)) == [
        date(2010, 7, 31),
    ]


def test_build_rows_membership_uses_the_months_final_session(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings, "INDEX_VALUATION_SEC_TICKERS_PATH", str(tmp_path / "missing.json.gz")
    )
    memberships = [
        # Exits on the month's final session: still a member for that month.
        {"ticker": "EXIT.US", "effective_from": date(2010, 1, 1), "effective_to": date(2010, 5, 28)},
        # Join effective only after the final session: not yet a member.
        {"ticker": "JOIN.US", "effective_from": date(2010, 5, 31), "effective_to": None},
        {"ticker": "AAPL.US", "effective_from": date(2010, 1, 1), "effective_to": None},
    ]
    per_ticker = {
        "EXIT.US": {"2010-05-31": _point("2010-05-28", 300.0, 15.0)},
        "JOIN.US": {"2010-05-31": _point("2010-05-28", 100.0, 10.0)},
        "AAPL.US": {"2010-05-31": _point("2010-05-28", 900.0, 45.0)},
    }
    # May 2010's calendar month-end is Memorial Day; the final session is the
    # 28th, so membership must be evaluated there, not on the label date.
    rows = build_index_valuation_rows(
        per_ticker,
        memberships,
        [date(2010, 5, 31)],
        min_members=1,
        min_coverage=0.5,
    )
    assert rows[0]["member_count"] == 2
    assert rows[0]["covered_count"] == 2
    assert rows[0]["equity_total"] == pytest.approx(1200.0)
    assert rows[0]["earnings_ttm_total"] == pytest.approx(60.0)


def test_build_rows_multi_class_membership_and_loss_makers(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings, "INDEX_VALUATION_SEC_TICKERS_PATH", str(tmp_path / "missing.json.gz")
    )
    memberships = [
        # Both Alphabet classes are members with same-month points: the
        # provider reports company-wide shares on every class, so the company
        # must enter the aggregate once, at the primary class's equity.
        {"ticker": "GOOG.US", "effective_from": date(2010, 1, 1), "effective_to": None},
        {"ticker": "GOOGL.US", "effective_from": date(2010, 1, 1), "effective_to": None},
        {"ticker": "AAPL.US", "effective_from": date(2010, 1, 1), "effective_to": None},
        {"ticker": "EXIT.US", "effective_from": date(2010, 1, 1), "effective_to": date(2011, 6, 30)},
        {"ticker": "LOSS.US", "effective_from": date(2010, 1, 1), "effective_to": None},
    ]
    per_ticker = {
        "GOOG.US": {"2011-06-30": _point("2011-06-30", 1000.0, 50.0), "2011-07-31": _point("2011-07-29", 1010.0, 51.0)},
        "GOOGL.US": {"2011-06-30": _point("2011-06-30", 500.0, 50.0), "2011-07-31": _point("2011-07-29", 505.0, 51.0)},
        "AAPL.US": {
            "2011-06-30": _point("2011-06-30", 800.0, 40.0),
            "2011-07-31": _point("2011-07-29", 810.0, 41.0),
        },
        "EXIT.US": {"2011-06-30": _point("2011-06-30", 300.0, 15.0)},
        "LOSS.US": {
            "2011-06-30": _point("2011-06-30", 200.0, -20.0),
            "2011-07-31": _point("2011-07-29", 200.0, -20.0),
        },
    }
    rows = build_index_valuation_rows(
        per_ticker,
        memberships,
        [date(2011, 6, 30), date(2011, 7, 31)],
        min_members=1,
        min_coverage=0.5,
    )
    june, july = rows

    # Alphabet counts once at the primary class's equity (1000), never the
    # 1500 class sum, while company earnings are counted exactly once.
    assert june["member_count"] == 4
    assert june["covered_count"] == 4
    assert june["equity_total"] == pytest.approx(1000.0 + 800.0 + 300.0 + 200.0)
    assert june["earnings_ttm_total"] == pytest.approx(50.0 + 40.0 + 15.0 - 20.0)
    assert june["index_pe"] == pytest.approx(2300.0 / 85.0)
    assert june["index_pe_earners"] == pytest.approx(2100.0 / 105.0)
    assert june["loss_maker_count"] == 1
    assert june["reason"] is None

    # After EXIT.US leaves the index it no longer contributes; the primary
    # class switch (GOOG still larger) keeps the company equity stable.
    assert july["member_count"] == 3
    assert july["equity_total"] == pytest.approx(1010.0 + 810.0 + 200.0)
    assert july["earnings_ttm_total"] == pytest.approx(51.0 + 41.0 - 20.0)

    quality = validate_index_valuation_rows(rows, min_month_coverage=0.9)
    assert quality["passed"] is True
    assert quality["metrics"]["months_valid"] == 2


def test_build_rows_gaps_below_member_and_coverage_minimums(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings, "INDEX_VALUATION_SEC_TICKERS_PATH", str(tmp_path / "missing.json.gz")
    )
    memberships = [
        {"ticker": "AAA.US", "effective_from": date(2026, 1, 1), "effective_to": None},
        {"ticker": "BBB.US", "effective_from": date(2026, 1, 1), "effective_to": None},
    ]
    per_ticker = {
        # AAA has inputs; BBB never produces a usable point this month.
        "AAA.US": {"2026-02-28": _point("2026-02-27", 100.0, 10.0)},
        "BBB.US": {"2026-02-28": _point("2026-02-27", None, None)},
    }
    rows = build_index_valuation_rows(
        per_ticker,
        memberships,
        [date(2026, 2, 28)],
        min_members=3,
        min_coverage=0.5,
    )
    assert rows[0]["reason"] == "insufficient-members"
    assert rows[0]["index_pe"] is None and rows[0]["equity_total"] is None
    assert rows[0]["member_count"] == 2 and rows[0]["covered_count"] == 1

    rows = build_index_valuation_rows(
        per_ticker,
        memberships,
        [date(2026, 2, 28)],
        min_members=1,
        min_coverage=0.9,
    )
    assert rows[0]["reason"] == "insufficient-coverage"
    assert rows[0]["index_pe"] is None

    quality = validate_index_valuation_rows(rows, min_month_coverage=0.5)
    assert quality["passed"] is False
    assert quality["metrics"]["gap_reasons"]["insufficient-coverage"] == 1


def test_build_rows_conflicting_multi_class_earnings_stay_gaps(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings, "INDEX_VALUATION_SEC_TICKERS_PATH", str(tmp_path / "missing.json.gz")
    )
    memberships = [
        {"ticker": "FOXA.US", "effective_from": date(2026, 1, 1), "effective_to": None},
        {"ticker": "FOX.US", "effective_from": date(2026, 1, 1), "effective_to": None},
        {"ticker": "AAPL.US", "effective_from": date(2026, 1, 1), "effective_to": None},
    ]
    per_ticker = {
        "FOXA.US": {"2026-03-31": _point("2026-03-30", 100.0, 10.0)},
        # FOX reports company-wide earnings that disagree with FOXA's.
        "FOX.US": {"2026-03-31": _point("2026-03-30", 50.0, 4.0)},
        "AAPL.US": {"2026-03-31": _point("2026-03-30", 900.0, 45.0)},
    }
    rows = build_index_valuation_rows(
        per_ticker,
        memberships,
        [date(2026, 3, 31)],
        min_members=1,
        min_coverage=0.5,
    )
    assert rows[0]["covered_count"] == 1
    assert rows[0]["multi_class_conflicts"] == 1
    assert rows[0]["equity_total"] == pytest.approx(900.0)
    assert rows[0]["earnings_ttm_total"] == pytest.approx(45.0)


def test_build_rows_membership_requires_price_session_inside_interval(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings, "INDEX_VALUATION_SEC_TICKERS_PATH", str(tmp_path / "missing.json.gz")
    )
    memberships = [
        # Joins on the last calendar day but never trades before month end.
        {"ticker": "JOIN.US", "effective_from": date(2026, 2, 28), "effective_to": None},
        {"ticker": "AAPL.US", "effective_from": date(2026, 1, 1), "effective_to": None},
    ]
    per_ticker = {
        "JOIN.US": {"2026-02-28": _point("2026-02-27", 100.0, 10.0)},
        "AAPL.US": {"2026-02-28": _point("2026-02-27", 900.0, 45.0)},
    }
    rows = build_index_valuation_rows(
        per_ticker,
        memberships,
        [date(2026, 2, 28)],
        min_members=1,
        min_coverage=0.5,
    )
    assert rows[0]["covered_count"] == 1
    assert rows[0]["equity_total"] == pytest.approx(900.0)


async def _seed_index_valuation_dependencies(db_session, target: date) -> None:
    for dataset in ("price_history", HISTORICAL_UNIVERSE_DATASET):
        run = PipelineRun(
            pipeline_name=f"{dataset}_fixture",
            target_date=target,
            status="published",
            stage="published",
        )
        db_session.add(run)
        await db_session.flush()
        db_session.add(DataPublication(
            dataset=dataset,
            as_of_date=target,
            pipeline_run_id=run.id,
            status="published",
        ))
    await db_session.commit()


def _statement_payloads(net_income: float, revenue: float = 1000.0):
    income = {
        "netIncome": net_income,
        "netIncomeApplicableToCommonShares": net_income,
        "totalRevenue": revenue,
        "currency_symbol": "USD",
    }
    balance = {
        "commonStockSharesOutstanding": None,  # replaced per ticker below
        "currency_symbol": "USD",
    }
    cash = {"currency_symbol": "USD"}
    return income, balance, cash


async def _seed_member(
    db_session,
    ticker: str,
    *,
    shares: float,
    price: float,
    quarterly_net_income: float,
    sector: str = "Technology",
    effective_from: date = date(2026, 1, 1),
):
    db_session.add(Ticker(
        ticker=ticker,
        currency="USD",
        sector=sector,
        last_updated=datetime(2026, 7, 1),
    ))
    db_session.add(UniverseMembership(
        universe="SP500",
        ticker=ticker,
        effective_from=effective_from,
        effective_to=None,
        source=HISTORICAL_UNIVERSE_SOURCE,
    ))
    await persist_full_split_history(db_session, ticker, [], date(2026, 7, 31))
    quarter_ends = [date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31)]
    for index, period_end in enumerate(quarter_ends):
        filed = datetime.combine(period_end + timedelta(days=40), datetime.min.time())
        income, balance, cash = _statement_payloads(quarterly_net_income)
        balance["commonStockSharesOutstanding"] = shares
        for section in (income, balance, cash):
            section["filing_date"] = filed.date().isoformat()
        db_session.add(FundamentalVersion(
            ticker=ticker,
            period_end=period_end,
            period_type="Quarterly",
            filing_at=filed,
            available_at=filed,
            availability_estimated=False,
            fetched_at=filed,
            revision=1,
            income_statement=income,
            balance_sheet=balance,
            cash_flow=cash,
            source="EODHD",
        ))
    for price_date in (date(2026, 5, 29), date(2026, 6, 30), date(2026, 7, 31)):
        db_session.add(DailyPrice(
            ticker=ticker,
            date=price_date,
            close=price,
            adjusted_close=price,
            volume=1_000_000,
        ))
    await db_session.commit()


@pytest.fixture
def valuation_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "INDEX_VALUATION_HISTORY_START", date(2026, 5, 1))
    monkeypatch.setattr(
        settings, "INDEX_VALUATION_SEC_TICKERS_PATH", str(tmp_path / "missing.json.gz")
    )
    monkeypatch.setattr(settings, "PIPELINE_MIN_INDEX_VALUATION_COVERAGE", 0.5)
    monkeypatch.setattr(settings, "INDEX_VALUATION_MIN_MONTH_COVERAGE", 0.5)


@pytest.mark.asyncio
async def test_refresh_publishes_and_serves_index_valuation(
    db_session, monkeypatch, valuation_settings
):
    target = date(2026, 7, 31)
    await _seed_index_valuation_dependencies(db_session, target)
    # equity/TTM earnings: AAA 1000/50, BBB (loss maker) 200/-20, CCC 200/20.
    await _seed_member(db_session, "AAA.US", shares=100, price=10.0, quarterly_net_income=12.5)
    await _seed_member(db_session, "BBB.US", shares=50, price=4.0, quarterly_net_income=-5.0)
    await _seed_member(db_session, "CCC.US", shares=40, price=5.0, quarterly_net_income=5.0)
    # A session after the target must never leak into the series: without the
    # through cap it would become the split reference and an August point.
    db_session.add(DailyPrice(
        ticker="AAA.US", date=date(2026, 8, 3), close=999.0, adjusted_close=999.0, volume=1,
    ))
    await db_session.commit()

    from services.valuation_history import get_valuation_history as load_history

    capped = await load_history("AAA.US", db_session, "1mo", through=target)
    assert capped["points"][-1]["date"] == "2026-07-31"
    assert capped["points"][-1]["price_date"] == "2026-07-31"
    uncapped = await load_history("AAA.US", db_session, "1mo")
    assert uncapped["points"][-1]["date"] == "2026-08-31"

    result = await refresh_index_valuation(target)
    assert result["status"] == "published"
    assert result["months_valid"] == 3
    assert result["cik_map_available"] is False

    snapshots = list((await db_session.execute(
        select(IndexValuationSnapshot).order_by(IndexValuationSnapshot.date)
    )).scalars())
    # The running month (August) is never published with a future label.
    assert [row.date.isoformat() for row in snapshots] == [
        "2026-05-31", "2026-06-30", "2026-07-31",
    ]
    for row in snapshots:
        assert row.member_count == 3
        assert row.covered_count == 3
        assert row.loss_maker_count == 1
        assert row.coverage_pct == pytest.approx(100.0)
        assert row.equity_total == pytest.approx(1400.0)
        assert row.earnings_ttm_total == pytest.approx(50.0)
        assert row.index_pe == pytest.approx(28.0)
        assert row.index_pe_earners == pytest.approx(1200.0 / 70.0)
        assert row.median_pe == pytest.approx(15.0)
        assert row.reason is None

    # Idempotent re-run for the same session.
    assert (await refresh_index_valuation(target))["status"] == "skipped"

    payload = await get_index_valuation(db_session, "SP500")
    validated = TypeAdapter(IndexValuationResponse).validate_python(payload)
    assert validated.meta.membership_mode == "point_in_time"
    assert validated.meta.history_basis == "reconstructed_estimates"
    assert validated.meta.history_start == date(2026, 5, 31)
    assert validated.stats.months_total == 3
    assert validated.stats.months_valid == 3
    assert validated.stats.latest_index_pe == pytest.approx(28.0)
    assert validated.stats.median_index_pe == pytest.approx(28.0)
    assert validated.points[-1].loss_maker_count == 1
    assert validated.points[-1].reason is None
    # The missing CIK cache is disclosed, not silently degraded.
    assert any("SEC company-tickers" in note for note in payload["meta"]["warnings"])

    from main import app

    with TestClient(app) as client:
        response = client.get("/api/v1/index-valuation", params={"universe": "SP500"})
        assert response.status_code == 200
        body = response.json()
        assert body["meta"]["universe"] == "SP500"
        assert len(body["points"]) == 3
        assert body["stats"]["latest_index_pe"] == pytest.approx(28.0)
        assert len(body["methodology"]) >= 5

        rejected = client.get("/api/v1/index-valuation", params={"universe": "NASDAQ100"})
        assert rejected.status_code == 422


@pytest.mark.asyncio
async def test_refresh_defers_without_dependency_publications(db_session, valuation_settings):
    result = await refresh_index_valuation(date(2026, 7, 31))
    assert result["status"] == "deferred"
    assert result["reason"] == "missing-publications"
    assert "price_history" in result["missing"]


@pytest.mark.asyncio
async def test_quality_gate_failure_publishes_no_partial_snapshot(
    db_session, valuation_settings
):
    target = date(2026, 7, 31)
    await _seed_index_valuation_dependencies(db_session, target)
    # Only one of three members has reconstructed inputs; coverage stays a gap.
    await _seed_member(db_session, "AAA.US", shares=100, price=10.0, quarterly_net_income=12.5)
    for ticker in ("BBB.US", "CCC.US"):
        db_session.add(Ticker(ticker=ticker, currency="USD", sector="Technology"))
        db_session.add(UniverseMembership(
            universe="SP500",
            ticker=ticker,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            source=HISTORICAL_UNIVERSE_SOURCE,
        ))
    await db_session.commit()

    with pytest.raises(ValueError, match="quality gate failed"):
        await refresh_index_valuation(target)

    assert await db_session.scalar(select(func.count(IndexValuationSnapshot.id))) == 0
    assert await db_session.scalar(
        select(func.count(DataPublication.id)).where(
            DataPublication.dataset == INDEX_VALUATION_DATASET
        )
    ) == 0
    failed_run = (await db_session.execute(
        select(PipelineRun).where(PipelineRun.pipeline_name == "index_valuation")
        .order_by(PipelineRun.id.desc())
    )).scalars().first()
    assert failed_run.status == "failed"


@pytest.mark.asyncio
async def test_serving_requires_publication_and_supported_universe(db_session, valuation_settings):
    with pytest.raises(IndexValuationUnavailable):
        await get_index_valuation(db_session, "SP500")
    with pytest.raises(IndexValuationUniverseUnavailable):
        await get_index_valuation(db_session, "RUSSELL2000")

    from main import app

    with TestClient(app) as client:
        assert client.get("/api/v1/index-valuation").status_code == 503


@pytest.mark.asyncio
async def test_retention_keeps_five_most_recent_publications(
    db_session, monkeypatch, valuation_settings
):
    target = date(2026, 7, 31)
    await _seed_index_valuation_dependencies(db_session, target)
    await _seed_member(db_session, "AAA.US", shares=100, price=10.0, quarterly_net_income=12.5)
    await _seed_member(db_session, "BBB.US", shares=50, price=4.0, quarterly_net_income=-5.0)

    for offset in range(6):
        old_target = date(2026, 7, 20 + offset)
        run = PipelineRun(
            pipeline_name="index_valuation",
            target_date=old_target,
            status="published",
            stage="published",
        )
        db_session.add(run)
        await db_session.flush()
        db_session.add(DataPublication(
            dataset=INDEX_VALUATION_DATASET,
            as_of_date=old_target,
            pipeline_run_id=run.id,
            status="published",
        ))
        db_session.add(IndexValuationSnapshot(
            pipeline_run_id=run.id,
            universe="SP500",
            date=old_target,
            member_count=2,
            covered_count=2,
            loss_maker_count=1,
            coverage_pct=100.0,
            equity_total=1200.0,
            earnings_ttm_total=30.0,
            index_pe=40.0,
        ))
    await db_session.commit()

    assert (await refresh_index_valuation(target))["status"] == "published"
    publications = list((await db_session.execute(
        select(DataPublication.as_of_date).where(
            DataPublication.dataset == INDEX_VALUATION_DATASET
        ).order_by(DataPublication.as_of_date.desc())
    )).scalars())
    assert len(publications) == 5
    assert publications[0] == target
    retained_runs = set((await db_session.execute(
        select(DataPublication.pipeline_run_id).where(
            DataPublication.dataset == INDEX_VALUATION_DATASET
        )
    )).scalars())
    orphan_count = await db_session.scalar(
        select(func.count(IndexValuationSnapshot.id)).where(
            IndexValuationSnapshot.pipeline_run_id.not_in(retained_runs)
        )
    )
    assert orphan_count == 0


def test_alembic_upgrade_creates_index_valuation_snapshots(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "index-valuation-migration.db"
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{database_path}",
        "ENVIRONMENT": "test",
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=project_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(index_valuation_snapshots)")
        }
        assert {"id", "pipeline_run_id", "universe", "date", "member_count", "covered_count",
                "loss_maker_count", "coverage_pct", "equity_total", "earnings_ttm_total",
                "earnings_ttm_earners", "index_pe", "index_pe_earners", "median_pe",
                "reason"} <= columns
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(index_valuation_snapshots)")
        }
        assert "ix_index_valuation_snapshots_run_universe_date" in indexes
