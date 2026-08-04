import os
import sqlite3
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import func, select

from api.schemas import MarketOverviewResponse
from models import (
    DataPublication,
    MarketBreadthSnapshot,
    PipelineRun,
    RRGPriceSnapshot,
    Ticker,
    UniverseMembership,
)
from services.market_breadth import (
    MARKET_BREADTH_DATASET,
    MarketOverviewUnavailable,
    MarketOverviewUniverseUnavailable,
    build_price_feature_frame,
    calculate_market_breadth_rows,
    effective_close,
    get_market_overview,
    market_sessions_through,
    refresh_market_breadth,
)
from services.rrg_prices import (
    RRG_BENCHMARK,
    RRG_PRICE_HISTORY_DATASET,
    RRG_PRICE_TICKERS,
    RRG_SECTOR_NAMES,
)
from services.universe import (
    HISTORICAL_UNIVERSE_DATASET,
    HISTORICAL_UNIVERSE_SOURCE,
    LIVE_UNIVERSE_SOURCE,
    parse_historical_memberships,
    replace_historical_memberships,
    refresh_historical_universe_memberships,
)


def test_alembic_upgrade_from_0003_adds_market_breadth_storage(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "market-migration.db"
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{database_path}",
        "ENVIRONMENT": "test",
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0003_expand_screener_snapshot"],
        cwd=project_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    with sqlite3.connect(database_path) as connection:
        connection.executemany(
            """
            INSERT INTO universe_membership (
                universe,
                ticker,
                effective_from,
                effective_to,
                observed_at,
                source,
                source_run_id
            ) VALUES (?, ?, ?, NULL, ?, ?, NULL)
            """,
            [
                (
                    "SP500",
                    "AAA.US",
                    "2025-01-10",
                    "2025-01-10 00:00:00",
                    "EODHD",
                ),
                (
                    "SP500",
                    "AAA.US",
                    "2025-01-10",
                    "2025-01-10 00:00:00",
                    LIVE_UNIVERSE_SOURCE,
                ),
                (
                    "RUSSELL2000",
                    "BBB.US",
                    "2025-01-10",
                    "2025-01-10 00:00:00",
                    "EODHD",
                ),
            ],
        )
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
            row[1]
            for row in connection.execute("PRAGMA table_info(market_breadth_snapshots)")
        }
        snapshot_indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(market_breadth_snapshots)")
        }
        membership_indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(universe_membership)")
        }
        membership_unique_columns = {
            tuple(
                column[2]
                for column in connection.execute(
                    f'PRAGMA index_info("{index_row[1]}")'
                )
            )
            for index_row in connection.execute(
                "PRAGMA index_list(universe_membership)"
            )
            if index_row[2]
        }
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        migrated_live_rows = connection.execute(
            """
            SELECT universe, ticker, source
            FROM universe_membership
            ORDER BY universe, ticker, source
            """
        ).fetchall()

    assert {
        "id",
        "pipeline_run_id",
        "universe",
        "date",
        "member_count",
        "price_count",
        "return_count",
        "advances",
        "declines",
        "unchanged",
        "ma20_eligible",
        "above_ma20",
        "ma50_eligible",
        "above_ma50",
        "ma200_eligible",
        "above_ma200",
        "high_low_eligible",
        "new_high_count",
        "new_low_count",
        "dispersion_1d",
    } == columns
    assert "ix_market_breadth_snapshots_run_universe_date" in snapshot_indexes
    assert "ix_universe_membership_universe_interval" in membership_indexes
    assert (
        "universe",
        "ticker",
        "effective_from",
        "source",
    ) in membership_unique_columns
    assert migrated_live_rows == [
        ("RUSSELL2000", "BBB.US", LIVE_UNIVERSE_SOURCE),
        ("SP500", "AAA.US", LIVE_UNIVERSE_SOURCE),
    ]
    assert revision == "0008_backfill_live_universe_source"


def test_historical_membership_parser_supports_duplicates_and_reentry():
    rows = [
        {"Code": "AAA", "StartDate": "2010-01-01", "EndDate": "2015-12-31"},
        {"Code": "AAA", "StartDate": "2010-01-01", "EndDate": "2015-12-31"},
        {"Code": "AAA", "StartDate": "2020-01-01", "EndDate": None},
        {"Code": "BBB.US", "StartDate": "2012-06-01", "EndDate": "0000-00-00"},
    ]

    parsed = parse_historical_memberships(rows, "SP500")

    assert len(parsed) == 3
    assert [row["effective_from"] for row in parsed if row["ticker"] == "AAA.US"] == [
        date(2010, 1, 1),
        date(2020, 1, 1),
    ]
    assert next(row for row in parsed if row["ticker"] == "BBB.US")["effective_to"] is None
    assert {row["source"] for row in parsed} == {HISTORICAL_UNIVERSE_SOURCE}


@pytest.mark.parametrize(
    "rows",
    [
        [
            {"Code": "AAA", "StartDate": "2020-01-01", "EndDate": "2021-06-30"},
            {"Code": "AAA", "StartDate": "2021-01-01", "EndDate": None},
        ],
        [
            {"Code": "AAA", "StartDate": "2020-01-01", "EndDate": None},
            {"Code": "AAA", "StartDate": "2022-01-01", "EndDate": None},
        ],
    ],
)
def test_historical_membership_parser_rejects_overlaps(rows):
    with pytest.raises(ValueError, match="Overlapping"):
        parse_historical_memberships(rows, "SP500")


def test_historical_membership_parser_only_ignores_unknown_start_before_window():
    required_from = date(2025, 1, 1)
    parsed = parse_historical_memberships(
        [
            {"Code": "OLD", "StartDate": None, "EndDate": "2020-01-01"},
            {"Code": "LIVE", "StartDate": "2024-01-01", "EndDate": None},
        ],
        "SP500",
        required_from=required_from,
    )
    assert [row["ticker"] for row in parsed] == ["LIVE.US"]

    with pytest.raises(ValueError, match="required history window"):
        parse_historical_memberships(
            [{"Code": "AMBIG", "StartDate": None, "EndDate": "2025-02-01"}],
            "SP500",
            required_from=required_from,
        )


@pytest.mark.asyncio
async def test_invalid_provider_history_does_not_replace_existing_intervals(
    db_session,
    monkeypatch,
):
    db_session.add(UniverseMembership(
        universe="SP500",
        ticker="OLD.US",
        effective_from=date(2010, 1, 1),
        source=HISTORICAL_UNIVERSE_SOURCE,
    ))
    await db_session.commit()

    @asynccontextmanager
    async def fake_client():
        yield object()

    async def fake_history(index_ticker, client=None):
        assert index_ticker == "GSPC.INDX"
        return None

    monkeypatch.setattr("services.universe.eodhd_client.create_http_client", fake_client)
    monkeypatch.setattr("services.universe.eodhd_client.get_index_component_history", fake_history)

    with pytest.raises(ValueError, match="SP500"):
        await refresh_historical_universe_memberships(date(2025, 1, 10))

    db_session.expire_all()
    memberships = list((await db_session.execute(select(UniverseMembership))).scalars())
    assert [(row.universe, row.ticker) for row in memberships] == [("SP500", "OLD.US")]
    assert not list((await db_session.execute(
        select(DataPublication).where(DataPublication.dataset == HISTORICAL_UNIVERSE_DATASET)
    )).scalars())


@pytest.mark.asyncio
async def test_historical_membership_publication_preserves_explicit_session_date(
    db_session,
    monkeypatch,
):
    target = date(2025, 1, 10)

    @asynccontextmanager
    async def fake_client():
        yield object()

    async def fake_history(index_ticker, client=None):
        assert index_ticker == "GSPC.INDX"
        return [
            {"Code": "SPA", "StartDate": "2020-01-01", "EndDate": None},
            {"Code": "SPB", "StartDate": "2020-01-01", "EndDate": None},
        ]

    monkeypatch.setattr("services.universe.eodhd_client.create_http_client", fake_client)
    monkeypatch.setattr("services.universe.eodhd_client.get_index_component_history", fake_history)

    result = await refresh_historical_universe_memberships(target)

    assert result["as_of_date"] == target.isoformat()
    publication = (await db_session.execute(
        select(DataPublication).where(
            DataPublication.dataset == HISTORICAL_UNIVERSE_DATASET
        )
    )).scalar_one()
    assert publication.as_of_date == target
    memberships = list((await db_session.execute(
        select(UniverseMembership).order_by(
            UniverseMembership.universe,
            UniverseMembership.ticker,
        )
    )).scalars())
    assert len(memberships) == 2
    assert {membership.universe for membership in memberships} == {"SP500"}
    assert {membership.source for membership in memberships} == {
        HISTORICAL_UNIVERSE_SOURCE
    }


@pytest.mark.asyncio
async def test_historical_and_live_memberships_can_share_a_start_date(db_session):
    target = date(2025, 1, 10)
    run = PipelineRun(
        pipeline_name="historical_universe_sync",
        target_date=target,
        status="running",
        stage="publishing_history",
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(UniverseMembership(
        universe="SP500",
        ticker="AAA.US",
        effective_from=target,
        source=LIVE_UNIVERSE_SOURCE,
    ))
    await db_session.flush()

    await replace_historical_memberships(
        db_session,
        "SP500",
        [{
            "universe": "SP500",
            "ticker": "AAA.US",
            "effective_from": target,
            "effective_to": None,
            "source": HISTORICAL_UNIVERSE_SOURCE,
        }],
        source_run_id=run.id,
    )
    await db_session.commit()

    memberships = list((await db_session.execute(
        select(UniverseMembership).where(
            UniverseMembership.universe == "SP500",
            UniverseMembership.ticker == "AAA.US",
        )
    )).scalars())
    assert {membership.source for membership in memberships} == {
        HISTORICAL_UNIVERSE_SOURCE,
        LIVE_UNIVERSE_SOURCE,
    }


def test_adjusted_close_is_preferred_and_close_is_strict_fallback():
    assert effective_close(98, 100) == 98
    assert effective_close(None, 100) == 100
    assert effective_close(None, None) is None
    assert effective_close(float("nan"), 100) is None
    assert effective_close(-1, 100) is None


def test_fixed_prices_verify_breadth_new_extremes_and_population_dispersion():
    dates = [value.date() for value in pd.bdate_range("2024-01-02", periods=252)]
    price_rows = []
    for index, price_date in enumerate(dates):
        price_rows.extend([
            {"ticker": "AAA.US", "date": price_date, "close": index + 1.0},
            {"ticker": "BBB.US", "date": price_date, "close": 252.0 - index},
        ])
    frame = build_price_feature_frame(price_rows)
    memberships = {
        "SP500": [
            {"ticker": "AAA.US", "effective_from": dates[0], "effective_to": None},
            {"ticker": "BBB.US", "effective_from": dates[0], "effective_to": None},
        ],
        "RUSSELL2000": [
            {"ticker": "BBB.US", "effective_from": dates[0], "effective_to": None},
        ],
    }

    calculated = calculate_market_breadth_rows(
        frame,
        memberships,
        [dates[-1]],
        ("SP500", "RUSSELL2000", "SP500_RUSSELL2000"),
    )
    sp500 = next(row for row in calculated if row["universe"] == "SP500")
    combined = next(row for row in calculated if row["universe"] == "SP500_RUSSELL2000")

    assert sp500["member_count"] == 2
    assert sp500["price_count"] == 2
    assert sp500["return_count"] == 2
    assert (sp500["advances"], sp500["declines"], sp500["unchanged"]) == (1, 1, 0)
    assert (sp500["ma20_eligible"], sp500["above_ma20"]) == (2, 1)
    assert (sp500["ma50_eligible"], sp500["above_ma50"]) == (2, 1)
    assert (sp500["ma200_eligible"], sp500["above_ma200"]) == (2, 1)
    assert sp500["high_low_eligible"] == 2
    assert (sp500["new_high_count"], sp500["new_low_count"]) == (1, 1)
    expected_returns = np.array([252 / 251 - 1, 1 / 2 - 1])
    assert sp500["dispersion_1d"] == pytest.approx(np.std(expected_returns, ddof=0))
    assert combined["member_count"] == 2  # BBB is present in both indexes and is deduplicated.


def test_one_day_return_requires_the_immediately_previous_expected_session():
    dates = [value.date() for value in pd.bdate_range("2025-01-02", periods=3)]
    frame = build_price_feature_frame(
        [
            {"ticker": "AAA.US", "date": dates[0], "close": 100.0},
            {"ticker": "AAA.US", "date": dates[2], "close": 110.0},
            {"ticker": "BBB.US", "date": dates[0], "close": 100.0},
            {"ticker": "BBB.US", "date": dates[1], "close": 101.0},
            {"ticker": "BBB.US", "date": dates[2], "close": 102.0},
        ],
        expected_dates=dates,
    ).set_index(["ticker", "date"])

    assert np.isnan(frame.loc[("AAA.US", pd.Timestamp(dates[2])), "return_1d"])
    assert frame.loc[("BBB.US", pd.Timestamp(dates[2])), "return_1d"] == pytest.approx(
        102 / 101 - 1
    )


async def _seed_overview_publication(db_session, target: date) -> None:
    breadth_run = PipelineRun(
        pipeline_name="market_breadth",
        target_date=target,
        status="published",
        stage="published",
        quality_report={"warnings": ["fixture warning"]},
    )
    rrg_run = PipelineRun(
        pipeline_name="rrg_price_history_backfill",
        target_date=target,
        status="published",
        stage="published",
    )
    db_session.add_all([breadth_run, rrg_run])
    await db_session.flush()
    db_session.add_all([
        DataPublication(
            dataset=MARKET_BREADTH_DATASET,
            as_of_date=target,
            pipeline_run_id=breadth_run.id,
            status="published",
        ),
        DataPublication(
            dataset=RRG_PRICE_HISTORY_DATASET,
            as_of_date=target,
            pipeline_run_id=rrg_run.id,
            status="published",
        ),
    ])
    sessions = market_sessions_through(target, 252)
    for universe, members in (("SP500", 500),):
        for index, session in enumerate(sessions):
            cycle = index % 3
            advances, declines, unchanged = (
                (members, 0, 0) if cycle == 0
                else ((0, members, 0) if cycle == 1 else (0, 0, members))
            )
            db_session.add(MarketBreadthSnapshot(
                pipeline_run_id=breadth_run.id,
                universe=universe,
                date=session,
                member_count=members,
                price_count=members,
                return_count=members,
                advances=advances,
                declines=declines,
                unchanged=unchanged,
                ma20_eligible=members,
                above_ma20=members // 2,
                ma50_eligible=members,
                above_ma50=members // 2 + 1,
                ma200_eligible=members,
                above_ma200=members // 2 - 1,
                high_low_eligible=members,
                new_high_count=members // 10,
                new_low_count=members // 20,
                dispersion_1d=0.01 + index / 100_000,
            ))
    db_session.add_all([Ticker(ticker=ticker) for ticker in RRG_PRICE_TICKERS])
    await db_session.flush()
    sector_offsets = {
        ticker: (index + 1) * 0.12
        for index, ticker in enumerate(RRG_SECTOR_NAMES)
    }
    for index, session in enumerate(sessions):
        for ticker in RRG_PRICE_TICKERS:
            if ticker == RRG_BENCHMARK:
                price = 100 + index
            elif ticker == "RSP.US":
                price = 100 + 1.5 * index
            else:
                price = 100 + sector_offsets[ticker] * index
            db_session.add(RRGPriceSnapshot(
                pipeline_run_id=rrg_run.id,
                ticker=ticker,
                date=session,
                close=price,
            ))
    await db_session.commit()


@pytest.mark.asyncio
async def test_market_overview_sp500_periods_alignment_and_formulas(
    db_session,
    monkeypatch,
):
    target = date(2026, 7, 31)
    monkeypatch.setattr(
        "services.market_breadth.latest_completed_us_session",
        lambda reference: date(2026, 8, 3),
    )
    await _seed_overview_publication(db_session, target)

    expected_lengths = {"3m": 63, "6m": 126, "1y": 252}
    for period, expected_length in expected_lengths.items():
        payload = await get_market_overview(db_session, "SP500", period)
        validated = TypeAdapter(MarketOverviewResponse).validate_python(payload)
        assert validated.meta.membership_mode == "point_in_time"
        assert validated.meta.stale is True
        assert validated.meta.warnings == ["fixture warning"]
        assert len(validated.dates) == expected_length
        assert len(validated.sector_trends) == 11
        assert all(
            len(series) == expected_length
            for sector in validated.sector_trends
            for series in (sector.absolute_index, sector.relative_to_spy_index)
        )
        assert len(validated.rsp_spy_index) == expected_length
        for values in validated.breadth.model_dump().values():
            assert len(values) == expected_length

    from main import app

    with TestClient(app) as client:
        for period, expected_length in expected_lengths.items():
            response = client.get(
                "/api/v1/market-overview",
                params={"universe": "SP500", "period": period},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["meta"]["membership_mode"] == "point_in_time"
            assert len(body["dates"]) == expected_length
            assert all(
                len(values) == expected_length
                for values in body["breadth"].values()
            )

        for universe in ("RUSSELL2000", "SP500_RUSSELL2000"):
            response = client.get(
                "/api/v1/market-overview",
                params={"universe": universe, "period": "1y"},
            )
            assert response.status_code == 422
            assert "temporarily unavailable" in response.json()["detail"]

    for universe in ("RUSSELL2000", "SP500_RUSSELL2000"):
        with pytest.raises(MarketOverviewUniverseUnavailable):
            await get_market_overview(db_session, universe, "1y")

    payload = await get_market_overview(db_session, "SP500", "1y")
    final_index = 251
    expected_rsp_spy = 100 * (
        (100 + 1.5 * final_index) / (100 + final_index)
    )
    assert payload["rsp_spy_index"][-1] == pytest.approx(expected_rsp_spy)

    net = pd.Series([100.0, -100.0, 0.0] * 84)
    expected_mcclellan = (
        net.ewm(span=19, adjust=False, min_periods=19).mean()
        - net.ewm(span=39, adjust=False, min_periods=39).mean()
    )
    assert payload["breadth"]["mcclellan"][-1] == pytest.approx(expected_mcclellan.iloc[-1])
    dispersion = pd.Series([0.01 + index / 100_000 for index in range(252)])
    expected_dispersion = dispersion.ewm(span=20, adjust=False, min_periods=20).mean()
    assert payload["breadth"]["dispersion_20d"][-1] == pytest.approx(expected_dispersion.iloc[-1])
    assert payload["breadth"]["new_high_pct"][-1] == pytest.approx(10)
    assert payload["breadth"]["new_low_pct"][-1] == pytest.approx(5)
    assert payload["breadth"]["new_high_low_pct"][-1] == pytest.approx(5)


@pytest.mark.asyncio
async def test_market_overview_without_successful_publication_is_unavailable(db_session):
    with pytest.raises(MarketOverviewUnavailable):
        await get_market_overview(db_session, "SP500", "1y")

    from main import app

    with TestClient(app) as client:
        response = client.get("/api/v1/market-overview")
    assert response.status_code == 503


async def _seed_dependencies(db_session, target: date) -> None:
    for dataset in ("price_history", HISTORICAL_UNIVERSE_DATASET, RRG_PRICE_HISTORY_DATASET):
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


@pytest.mark.asyncio
async def test_quality_gate_failure_publishes_no_partial_market_snapshot(db_session, monkeypatch):
    target = date(2025, 1, 10)
    await _seed_dependencies(db_session, target)
    monkeypatch.setattr("services.market_breadth.calculate_market_breadth_rows", lambda *args: [])
    monkeypatch.setattr(
        "services.market_breadth.validate_market_breadth_rows",
        lambda rows: {
            "passed": False,
            "metrics": {"minimum_price_coverage": 0.5},
            "errors": ["price coverage below 95%"],
            "warnings": [],
        },
    )

    with pytest.raises(ValueError, match="quality gate failed"):
        await refresh_market_breadth(target)

    assert await db_session.scalar(select(func.count(MarketBreadthSnapshot.id))) == 0
    assert await db_session.scalar(
        select(func.count(DataPublication.id)).where(
            DataPublication.dataset == MARKET_BREADTH_DATASET
        )
    ) == 0


@pytest.mark.asyncio
async def test_market_breadth_retains_only_five_complete_publications(db_session, monkeypatch):
    monkeypatch.setattr("services.market_breadth.MARKET_BREADTH_DISPLAY_SESSIONS", 2)

    def fake_rows(price_frame, memberships, display_dates):
        return [
            {
                "universe": universe,
                "date": session,
                "member_count": 2,
                "price_count": 2,
                "return_count": 2,
                "advances": 1,
                "declines": 1,
                "unchanged": 0,
                "ma20_eligible": 2,
                "above_ma20": 1,
                "ma50_eligible": 2,
                "above_ma50": 1,
                "ma200_eligible": 2,
                "above_ma200": 1,
                "high_low_eligible": 2,
                "new_high_count": 1,
                "new_low_count": 0,
                "dispersion_1d": 0.01,
            }
            for universe in ("SP500",)
            for session in display_dates
        ]

    monkeypatch.setattr("services.market_breadth.calculate_market_breadth_rows", fake_rows)
    monkeypatch.setattr(
        "services.market_breadth.validate_market_breadth_rows",
        lambda rows: {
            "passed": True,
            "metrics": {"rows": len(rows)},
            "errors": [],
            "warnings": [],
        },
    )

    targets = market_sessions_through(date(2025, 1, 17), 6)
    for target in targets:
        await _seed_dependencies(db_session, target)
        result = await refresh_market_breadth(target)
        assert result["status"] == "published"

    db_session.expire_all()
    publications = list((await db_session.execute(
        select(DataPublication)
        .where(DataPublication.dataset == MARKET_BREADTH_DATASET)
        .order_by(DataPublication.as_of_date)
    )).scalars())
    assert [publication.as_of_date for publication in publications] == targets[-5:]
    assert await db_session.scalar(select(func.count(MarketBreadthSnapshot.id))) == 5 * 2
