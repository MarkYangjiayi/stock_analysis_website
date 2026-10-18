import os
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from api.schemas import EventsExpectationsResponse
from models import ScreenerFundamentalCache, Ticker
from services.decision_support import with_signal_warnings
from services.events_expectations import get_events_expectations
from services.expectation_signals import (
    build_expectation_context,
    estimate_revision_signal,
    extract_expectation_signals,
    load_expectation_signals,
    short_interest_signal,
    signal_warnings,
)
from services.raw_store import persist_snapshot
from services.screener_metrics import extract_fundamental_metrics
from services.watchlist_signals import expectation_signals, signal_as_of, signal_tags


def _payload(updated_at="2026-10-16", current_year=None, technicals=None, short_float=0.062):
    current_year = current_year or {
        "epsTrendCurrent": "4.80",
        "epsTrend30daysAgo": "5.00",
        "epsTrend90daysAgo": "5.20",
        "epsRevisionsUpLast30days": "2",
        "epsRevisionsDownLast30days": "9",
        "earningsEstimateNumberOfAnalysts": "20",
    }
    return {
        "General": {"UpdatedAt": updated_at},
        "Earnings": {"Trend": {
            # Earlier fiscal years keep the "0y" label; only the latest is current.
            "2025-12-31": {"date": "2025-12-31", "period": "0y", "epsTrendCurrent": "9.00",
                           "epsTrend30daysAgo": "1.00", "earningsEstimateNumberOfAnalysts": "20"},
            "2026-12-31": {"date": "2026-12-31", "period": "0y", **current_year},
            "2027-12-31": {"date": "2027-12-31", "period": "+1y", "epsTrendCurrent": "5.20",
                           "epsTrend30daysAgo": "5.40", "epsTrend90daysAgo": "5.60",
                           "earningsEstimateNumberOfAnalysts": "18"},
        }},
        "Technicals": technicals if technicals is not None else {
            "SharesShort": 13_000_000, "SharesShortPriorMonth": 10_000_000, "ShortRatio": 3.4,
        },
        "SharesStats": {"ShortPercentFloat": short_float},
    }


def test_extracts_current_fiscal_year_revisions_and_short_interest():
    signals = extract_expectation_signals(_payload())

    assert signals["as_of"] == date(2026, 10, 16)
    assert signals["eps_period_end"] == date(2026, 12, 31)
    assert signals["eps_revision_30d"] == pytest.approx(-0.04)
    assert signals["eps_revision_90d"] == pytest.approx(-0.4 / 5.2)
    assert signals["eps_revision_breadth_30d"] == pytest.approx(-0.35)
    assert signals["eps_estimate_analysts"] == 20
    assert signals["eps_next_year_revision_30d"] == pytest.approx(-0.2 / 5.4)
    assert signals["eps_next_year_revision_90d"] == pytest.approx(-0.4 / 5.6)
    assert signals["short_interest_change"] == pytest.approx(0.30)
    assert signals["short_float"] == pytest.approx(0.062)
    assert signals["short_ratio"] == pytest.approx(3.4)


def test_short_interest_falls_back_to_shares_stats():
    payload = _payload(technicals={})
    payload["SharesStats"].update(SharesShort=2_000_000, SharesShortPriorMonth=4_000_000)

    assert extract_expectation_signals(payload)["short_interest_change"] == pytest.approx(-0.5)


def test_stale_fiscal_year_placeholders_and_tiny_bases_are_unavailable():
    stale = extract_expectation_signals(_payload(updated_at="2027-06-01"))
    assert stale["eps_period_end"] is None and stale["eps_revision_30d"] is None

    placeholder = extract_expectation_signals(_payload(current_year={
        "epsTrendCurrent": "1.10", "epsTrend30daysAgo": "0", "epsTrend90daysAgo": "0.03",
        "earningsEstimateNumberOfAnalysts": "5",
    }))
    assert placeholder["eps_revision_30d"] is None
    assert placeholder["eps_revision_90d"] is None


def test_loss_estimates_are_revised_against_the_absolute_base():
    signals = extract_expectation_signals(_payload(current_year={
        "epsTrendCurrent": "-1.20", "epsTrend30daysAgo": "-1.00", "earningsEstimateNumberOfAnalysts": "8",
    }))

    assert signals["eps_revision_30d"] == pytest.approx(-0.20)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        # A fresh 30-day raise outranks an older 90-day cut.
        ({"eps_revision_30d": 0.05, "eps_revision_90d": -0.12}, {"direction": "raise", "basis": "30d", "severity": None}),
        ({"eps_revision_30d": -0.04, "eps_revision_90d": -0.05}, {"direction": "cut", "basis": "30d", "severity": "warning"}),
        ({"eps_revision_30d": -0.01, "eps_revision_90d": -0.25}, {"direction": "cut", "basis": "90d", "severity": "high"}),
        ({"eps_revision_30d": -0.11}, {"direction": "cut", "basis": "30d", "severity": "high"}),
        ({"eps_revision_30d": -0.01, "eps_revision_breadth_30d": -0.4, "eps_revisions_down_30d": 8},
         {"direction": "cut", "basis": "breadth", "severity": "warning"}),
        # Breadth needs at least three revisions in that direction.
        ({"eps_revision_30d": 0.0, "eps_revision_breadth_30d": -0.5, "eps_revisions_down_30d": 2}, None),
        ({"eps_revision_30d": 0.01, "eps_revision_90d": 0.02}, None),
    ],
)
def test_estimate_revision_direction_and_severity(values, expected):
    assert estimate_revision_signal({"eps_estimate_analysts": 20, **values}) == expected


def test_thin_coverage_is_not_assessed():
    assert estimate_revision_signal({"eps_estimate_analysts": 2, "eps_revision_30d": -0.5}) is None


def test_percentage_revisions_need_the_next_fiscal_year_to_agree():
    # A one-off gain lifts only the current year (Alphabet's FY2026 in Sept 2026).
    one_off = {"eps_estimate_analysts": 53, "eps_revision_30d": 0.001, "eps_revision_90d": 0.449,
               "eps_next_year_revision_30d": 0.006, "eps_next_year_revision_90d": 0.029}
    assert estimate_revision_signal(one_off) is None
    mixed = {"eps_estimate_analysts": 20, "eps_revision_30d": -0.05, "eps_next_year_revision_30d": 0.01}
    assert estimate_revision_signal(mixed) is None
    # Severity follows the smaller of the two cuts.
    both = {"eps_estimate_analysts": 20, "eps_revision_30d": -0.15, "eps_next_year_revision_30d": -0.06}
    assert estimate_revision_signal(both) == {"direction": "cut", "basis": "30d", "severity": "warning"}


@pytest.mark.parametrize(
    ("change", "short_float", "expected"),
    [
        (0.30, 0.06, {"kind": "rising", "severity": "warning"}),
        (0.60, 0.12, {"kind": "rising", "severity": "high"}),
        (0.60, 0.04, None),
        (-0.10, 0.18, {"kind": "crowded", "severity": "warning"}),
        (0.30, 0.30, {"kind": "rising", "severity": "high"}),
        (0.30, None, None),
    ],
)
def test_short_interest_rules(change, short_float, expected):
    assert short_interest_signal({"short_interest_change": change, "short_float": short_float}) == expected


def test_signal_warnings_carry_source_and_no_evidence_id():
    signals = extract_expectation_signals(_payload())
    warnings = {item["id"]: item for item in signal_warnings(signals, source_date="2026-10-16")}

    cut = warnings["estimate_cuts"]
    assert cut["severity"] == "warning"
    assert cut["evidence_id"] is None and cut["evidence_metric"] == "expectations"
    assert cut["category"] == "expectations" and cut["source_date"] == "2026-10-16"
    assert cut["message"] == (
        "Current-FY consensus EPS moved from 5.00 to 4.80 over 30 days (-4.0%; -7.7% over 90 days); "
        "next FY -3.7% over 30 days, -7.1% over 90 days; 2 up / 9 down revisions among 20 analysts in 30 days."
    )
    breadth_only = {**signals, "eps_revision_30d": -0.01, "eps_revision_90d": -0.02,
                    "eps_next_year_revision_30d": None, "eps_next_year_revision_90d": None}
    assert signal_warnings(breadth_only, source_date=None)[0]["message"] == (
        "Net 35% of covering analysts cut the current-FY EPS estimate "
        "(2 up / 9 down revisions among 20 analysts in 30 days); current FY -1.0% over 30 days, -2.0% over 90 days."
    )
    short = warnings["short_interest"]
    assert short["title"] == "Short interest rising"
    assert short["message"] == (
        "Short interest is 6.2% of float; shares sold short changed +30% month over month; 3.4 days to cover."
    )


def test_breadth_cut_message_shows_the_change_that_sets_its_severity():
    # ASTS on finbrain.icu (2026-10-11): breadth triggered the cut, the 90-day change made it high.
    signals = {"eps_estimate_analysts": 8, "eps_revisions_up_30d": 0, "eps_revisions_down_30d": 4,
               "eps_revision_breadth_30d": -0.5, "eps_revision_30d": 0.0001, "eps_revision_90d": -0.517,
               "eps_next_year_revision_30d": -0.084, "eps_next_year_revision_90d": -0.843}
    warning = signal_warnings(signals, source_date="2026-10-11")[0]

    assert warning["severity"] == "high"
    assert warning["message"] == (
        "Net 50% of covering analysts cut the current-FY EPS estimate "
        "(0 up / 4 down revisions among 8 analysts in 30 days); current FY 0.0% over 30 days, "
        "-51.7% over 90 days; next FY -8.4% over 30 days, -84.3% over 90 days."
    )


def test_screener_metrics_include_expectation_fields():
    metrics = extract_fundamental_metrics(_payload())

    assert metrics["eps_revision_30d"] == pytest.approx(-0.04)
    assert metrics["eps_revision_breadth_30d"] == pytest.approx(-0.35)
    assert metrics["eps_estimate_analysts"] == 20
    assert metrics["short_interest_change"] == pytest.approx(0.30)
    assert metrics["short_ratio"] == pytest.approx(3.4)


def test_signal_warnings_join_key_risks_without_touching_statement_rules():
    statement_rule = {"id": "revenue_decline", "severity": "warning", "evidence_id": "E27"}
    risks = {"warnings": [statement_rule], "data_quality_notes": [], "high_count": 0, "warning_count": 1}
    context = {"available": True, "as_of": "2026-10-16", "signals": extract_expectation_signals(_payload(short_float=0.30))}

    merged = with_signal_warnings(risks, context)

    assert [item["id"] for item in merged["warnings"]] == ["short_interest", "estimate_cuts", "revenue_decline"]
    assert merged["high_count"] == 1 and merged["warning_count"] == 2
    assert merged["warnings"][2]["category"] == "fundamental"
    assert "category" not in statement_rule
    assert with_signal_warnings(risks, {"available": False, "signals": None})["warnings"] == [
        {**statement_rule, "category": "fundamental"}
    ]


@pytest.mark.asyncio
async def test_signals_load_from_the_latest_snapshot_and_expire(db_session):
    await persist_snapshot(db_session, "EODHD", "fundamentals", _payload(), details={"ticker": "SIG.US"})

    fresh = await load_expectation_signals("SIG.US", db_session, date(2026, 10, 18))
    assert fresh["available"] is True
    assert fresh["as_of"] == "2026-10-16"
    assert fresh["estimate_signal"] == {"direction": "cut", "basis": "30d", "severity": "warning"}
    assert fresh["short_signal"] == {"kind": "rising", "severity": "warning"}

    stale = await load_expectation_signals("SIG.US", db_session, date(2026, 11, 30))
    assert stale["available"] is False
    assert "45 days old" in stale["notes"][0]

    missing = await load_expectation_signals("NONE.US", db_session, date(2026, 10, 18))
    assert missing["available"] is False and missing["signals"] is None


@pytest.mark.asyncio
async def test_events_panel_reaches_the_same_conclusion_as_key_risks(db_session):
    await persist_snapshot(db_session, "EODHD", "fundamentals", _payload(), details={"ticker": "SIG.US"})
    reference = date(2026, 10, 18)

    events = await get_events_expectations("SIG.US", db_session, reference)
    key_risks = await load_expectation_signals("SIG.US", db_session, reference)

    assert events["expectation_signals"] == key_risks
    assert events["expectation_signals"]["estimate_assessed"] is True
    # The response model must keep the block, not silently drop it.
    served = EventsExpectationsResponse.model_validate(events).model_dump(mode="json")
    assert served["expectation_signals"]["estimate_signal"] == {"direction": "cut", "basis": "30d", "severity": "warning"}
    assert served["expectation_signals"]["signals"]["eps_period_end"] == "2026-12-31"

    stale = await get_events_expectations("SIG.US", db_session, date(2026, 11, 30))
    assert stale["expectation_signals"]["available"] is False
    assert stale["expectation_signals"]["estimate_assessed"] is False
    assert (await get_events_expectations("NONE.US", db_session, reference))["expectation_signals"]["available"] is False


def test_thin_coverage_is_reported_as_not_assessed():
    payload = _payload(current_year={"epsTrendCurrent": "1.00", "epsTrend30daysAgo": "1.20",
                                     "earningsEstimateNumberOfAnalysts": "2"})
    context = build_expectation_context(SimpleNamespace(fetched_at=None), payload, date(2026, 10, 18))

    assert context["available"] is True and context["estimate_assessed"] is False
    assert context["estimate_signal"] is None
    assert "Fewer than 3 analysts" in context["notes"][0]


def test_report_tags_name_the_signal_and_its_date():
    signals = {
        "items": {"AAA.US": {"as_of": "2026-10-16", "rsi": 25.0}},
        "oversold": 30, "overbought": 70,
        "expectations": {"items": {
            "AAA.US": {"as_of": "2026-10-14", "eps_revision_30d": -0.042, "short_interest_change": 0.31,
                       "short_float": 0.06, "estimate": {"direction": "cut", "basis": "30d", "severity": "warning"},
                       "short": {"kind": "rising", "severity": "warning"}},
            "BBB.US": {"as_of": "2026-10-14", "eps_revisions_up_30d": 6, "eps_estimate_analysts": 12,
                       "short_float": 0.21, "estimate": {"direction": "raise", "basis": "breadth", "severity": None},
                       "short": {"kind": "crowded", "severity": "warning"}},
            "CCC.US": {"as_of": "2026-10-14", "eps_revision_90d": -0.081,
                       "estimate": {"direction": "cut", "basis": "90d", "severity": "warning"}, "short": None},
        }},
    }

    assert signal_tags("AAA.US", signals) == [
        {"text": "RSI 25 超卖", "color": "blue"},
        {"text": "预期下修 4.2%", "color": "carmine"},
        {"text": "空头增加 31%", "color": "purple"},
    ]
    assert signal_tags("BBB.US", signals) == [
        {"text": "预期上修 6/12家", "color": "turquoise"},
        {"text": "空头占流通 21%", "color": "purple"},
    ]
    assert signal_tags("CCC.US", signals) == [{"text": "预期下修 8.1%·90天", "color": "carmine"}]
    assert signal_as_of("AAA.US", signals) == "截至 2026-10-16；预期数据截至 2026-10-14"
    assert signal_as_of("CCC.US", signals) == "预期数据截至 2026-10-14"


@pytest.mark.asyncio
async def test_report_evidence_reads_each_symbols_latest_payload(db_session):
    await persist_snapshot(db_session, "EODHD", "fundamentals", _payload(), details={"ticker": "SIG.US"})
    await db_session.commit()

    evidence = await expectation_signals(["SIG.US", "NONE.US", "SIG.US"], date(2026, 10, 18))

    assert evidence["unavailable"] == ["NONE.US"]
    item = evidence["items"]["SIG.US"]
    assert item["as_of"] == "2026-10-16"
    assert item["eps_revision_30d"] == pytest.approx(-0.04)
    assert item["estimate"]["direction"] == "cut"
    assert signal_tags("SIG.US", {"expectations": evidence}) == [
        {"text": "预期下修 4.0%", "color": "carmine"},
        {"text": "空头增加 30%", "color": "purple"},
    ]


@pytest.mark.asyncio
async def test_rederive_script_fills_cached_rows_from_stored_payloads(db_session):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from rederive_expectation_signals import rederive

    snapshot = await persist_snapshot(db_session, "EODHD", "fundamentals", _payload(), details={"ticker": "SIG.US"})
    db_session.add_all([Ticker(ticker="SIG.US"), Ticker(ticker="OLD.US")])
    await db_session.flush()
    db_session.add_all([
        ScreenerFundamentalCache(ticker="SIG.US", as_of_date=date(2026, 10, 16), normalized_data={"pe_ratio": 20.0},
                                 raw_snapshot_id=snapshot.id, source="EODHD"),
        ScreenerFundamentalCache(ticker="OLD.US", as_of_date=date(2026, 10, 16), normalized_data={"pe_ratio": 9.0},
                                 raw_snapshot_id=None, source="EODHD"),
    ])
    await db_session.commit()

    assert (await rederive(dry_run=True))["updated"] == 1
    counts = await rederive()
    assert counts == {"cached": 2, "updated": 1, "already_present": 0, "no_raw_snapshot": 1, "unreadable": 0}

    db_session.expire_all()
    row = (await db_session.execute(
        select(ScreenerFundamentalCache).where(ScreenerFundamentalCache.ticker == "SIG.US")
    )).scalar_one()
    assert row.normalized_data["pe_ratio"] == 20.0
    assert row.normalized_data["eps_revision_30d"] == pytest.approx(-0.04)
    assert row.as_of_date == date(2026, 10, 16)
    assert (await rederive())["already_present"] == 1


def test_migration_adds_and_removes_signal_columns(tmp_path):
    path = tmp_path / "screener.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
        db.execute("INSERT INTO alembic_version VALUES ('0024_drop_backtest_tables')")
        db.execute("CREATE TABLE stock_screener_snapshot (id INTEGER PRIMARY KEY, ticker VARCHAR, date DATE, short_float NUMERIC)")
        db.execute("INSERT INTO stock_screener_snapshot VALUES (1, 'AAA.US', '2026-10-16', 0.05)")
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{path}", "ENVIRONMENT": "test"}
    root = Path(__file__).resolve().parents[1]

    def alembic(*args):
        subprocess.run([sys.executable, "-m", "alembic", *args], cwd=root, env=env, check=True, capture_output=True)

    def columns():
        with sqlite3.connect(path) as db:
            return {row[1] for row in db.execute("PRAGMA table_info(stock_screener_snapshot)")}

    alembic("upgrade", "0025_expectation_signals")
    added = {"eps_revision_30d", "eps_revision_90d", "eps_revision_breadth_30d",
             "eps_estimate_analysts", "short_interest_change", "short_ratio"}
    assert added <= columns()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT ticker, short_float, short_ratio FROM stock_screener_snapshot").fetchone() == ("AAA.US", 0.05, None)
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0025_expectation_signals"

    alembic("downgrade", "0024_drop_backtest_tables")
    assert columns().isdisjoint(added)
