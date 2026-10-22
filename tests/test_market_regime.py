from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from database import async_session_maker
from models import StockScreenerSnapshot, Theme, ThemeMember
from services import credit_spreads, daily_reporter, market_regime
from services.anomaly_clusters import cluster_anomalies, collect_anomaly_clusters
from services.credit_spreads import CreditSpreadUnavailable, get_hy_oas, parse_fred_csv, summarize_hy_oas
from services.market_regime import (
    breadth_reading,
    build_regime,
    credit_reading,
    curve_reading,
    participation_reading,
    trend_reading,
    valuation_reading,
    volatility_reading,
)

NOW = datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc)


def _overview(closes, breadth=None, ratio=None, stale=False):
    return {
        "meta": {"as_of_date": "2026-10-16", "stale": stale},
        "dates": ["2026-10-15", "2026-10-16"],
        "benchmark": {"absolute_index": closes},
        "breadth": {"pct_above_ma200": breadth or []},
        "rsp_spy_index": ratio or [],
    }


def _close_quote(ticker, price, previous_close, state="已收盘", status="available", previous_date="2026-10-16"):
    """A report quote row as services/report_market.py stores it for the 2026-10-19 session."""
    return {"ticker": ticker, "price": price, "previous_close": previous_close, "status": status,
            "session_date": "2026-10-19",
            "session": {"state": state, "expected_date": "2026-10-19", "previous_date": previous_date}}


# --- FRED high-yield spread ------------------------------------------------


def test_fred_csv_skips_missing_days():
    content = "observation_date,BAMLH0A0HYM2\n2026-10-13,3.16\n2026-10-14,.\n2026-10-15,3.23\nbad,1\n"
    assert parse_fred_csv(content) == [["2026-10-13", 3.16], ["2026-10-15", 3.23]]


def test_hy_oas_summary_reports_month_change_and_midrank_percentile():
    observations = [[(date(2026, 8, 1) + timedelta(days=day)).isoformat(), 3.0 + day / 100] for day in range(76)]
    summary = summarize_hy_oas(observations)

    assert summary["as_of"] == "2026-10-15" and summary["value_pct"] == pytest.approx(3.75)
    # 2026-09-15 is 30 days earlier: 3.45.
    assert summary["change_1m_pt"] == pytest.approx(0.30)
    assert summary["percentile"] == pytest.approx(100 * 75.5 / 76, abs=0.1)


@pytest.mark.asyncio
async def test_hy_oas_uses_a_fresh_cache_and_survives_a_failed_refresh(monkeypatch):
    calls = []

    async def fetch(client=None):
        calls.append(1)
        return [["2026-09-15", 3.0], ["2026-10-15", 3.5]]

    monkeypatch.setattr(credit_spreads, "fetch_hy_oas", fetch)
    first = await get_hy_oas(now=NOW)
    await get_hy_oas(now=NOW + timedelta(hours=2))
    assert len(calls) == 1 and first["value_pct"] == 3.5 and first["from_cache_after_failure"] is False

    async def failing(client=None):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(credit_spreads, "fetch_hy_oas", failing)
    stale = await get_hy_oas(now=NOW + timedelta(days=2))
    assert stale["value_pct"] == 3.5 and stale["from_cache_after_failure"] is True

    credit_spreads._cache_path().unlink()
    with pytest.raises(CreditSpreadUnavailable):
        await get_hy_oas(now=NOW + timedelta(days=3))


# --- regime rules ------------------------------------------------------------


@pytest.mark.parametrize(
    ("closes", "state"),
    [
        ([100 + day * 0.1 for day in range(252)], "上升"),
        ([200 - day * 0.1 for day in range(252)], "下降"),
        ([100 + (day % 20) for day in range(252)], "震荡"),
    ],
)
def test_trend_compares_price_with_the_50_and_200_day_averages(closes, state):
    reading = trend_reading(_overview(closes))
    assert reading["state"] == state
    assert reading["detail"].startswith("SPY 距 200 日线 ")


def test_trend_needs_200_sessions():
    assert trend_reading(_overview([100.0] * 199)) is None


@pytest.mark.parametrize(
    ("series", "state", "detail"),
    [
        ([70.0] * 5 + [64.0], "健康", "站上 200 日线 64%，一周 -6pt"),
        ([45.0] * 6, "中性", "站上 200 日线 45%，一周 +0pt"),
        ([39.0], "偏弱", "站上 200 日线 39%"),
    ],
)
def test_breadth_levels_and_weekly_change(series, state, detail):
    reading = breadth_reading(_overview([], breadth=series))
    assert (reading["state"], reading["detail"]) == (state, detail)


@pytest.mark.parametrize(
    ("end", "state", "detail"),
    [
        (102.0, "广泛", "等权跑赢市值加权 21 日 +2.0%"),
        (98.5, "集中", "等权跑输市值加权 21 日 -1.5%"),
        (100.5, "均衡", "等权跑赢市值加权 21 日 +0.5%"),
    ],
)
def test_participation_uses_the_21_session_equal_weight_ratio(end, state, detail):
    reading = participation_reading(_overview([], ratio=[100.0] * 21 + [end]))
    assert (reading["state"], reading["detail"]) == (state, detail)


def test_closing_quotes_extend_trend_and_participation_but_not_breadth():
    overview = _overview([100 + day * 0.1 for day in range(252)], breadth=[55.0] * 6, ratio=[100.0] * 22)
    # SPY falls 5% on the day while RSP gains 1%.
    quotes = [_close_quote("SPY.US", 95.0, 100.0), _close_quote("RSP.US", 101.0, 100.0)]

    closing = market_regime.with_session_close(overview, quotes)
    regime = build_regime(overview=overview, closing_overview=closing, vix=None, vix_as_of=None,
                          hy_oas=None, treasury=None, valuation=None)
    items = {item["key"]: item for item in regime["items"]}

    assert (items["trend"]["state"], items["trend"]["as_of"]) == ("震荡", "2026-10-19")
    assert items["trend"]["detail"] == "SPY 距 200 日线 +3.1%"
    assert (items["participation"]["state"], items["participation"]["detail"], items["participation"]["as_of"]) == (
        "广泛", "等权跑赢市值加权 21 日 +6.3%", "2026-10-19")
    assert items["breadth"]["as_of"] == "2026-10-16"
    # The published overview itself is left as it was.
    assert overview["meta"]["as_of_date"] == "2026-10-16" and len(overview["benchmark"]["absolute_index"]) == 252


@pytest.mark.parametrize(
    "change",
    [
        {"state": "盘中"},  # a morning report's intraday quote
        {"status": "stale"},
        {"previous_date": "2026-10-15"},  # the overview missed a session
    ],
)
def test_closing_quotes_must_be_the_close_of_the_next_session(change):
    overview = _overview([100.0] * 252, ratio=[100.0] * 22)
    quotes = [_close_quote("SPY.US", 95.0, 100.0, **change), _close_quote("RSP.US", 101.0, 100.0)]
    assert market_regime.with_session_close(overview, quotes) is None


def test_closing_quotes_need_both_spy_and_rsp():
    overview = _overview([100.0] * 252, ratio=[100.0] * 22)
    assert market_regime.with_session_close(overview, [_close_quote("SPY.US", 95.0, 100.0)]) is None


@pytest.mark.parametrize(("vix", "state"), [(13.0, "平静"), (17.5, "正常"), (24.0, "偏高"), (35.0, "恐慌")])
def test_volatility_bands(vix, state):
    assert volatility_reading(vix, "2026-10-16")["state"] == state
    assert volatility_reading(None, None) is None


@pytest.mark.parametrize(
    ("level", "change", "state"),
    [(3.2, 0.1, "平稳"), (3.4, 0.5, "走阔"), (4.8, 0.0, "走阔"), (6.2, 0.0, "紧张"), (3.5, 1.2, "紧张")],
)
def test_credit_states_combine_level_and_monthly_change(level, change, state):
    reading = credit_reading({"value_pct": level, "change_1m_pt": change, "percentile": 40.0, "as_of": "2026-10-15"})
    assert reading["state"] == state
    assert reading["detail"].startswith(f"高收益债利差 {level:.2f}%，一月 ")


@pytest.mark.parametrize(("spread", "state"), [(-20.0, "倒挂"), (33.0, "偏平"), (120.0, "正常")])
def test_curve_states(spread, state):
    assert curve_reading({"spread_10y_2y_bp": spread, "as_of": "2026-10-16"})["state"] == state


@pytest.mark.parametrize(("percentile", "state"), [(85.0, "偏贵"), (50.0, "中性"), (10.0, "便宜")])
def test_valuation_states(percentile, state):
    reading = valuation_reading({"percentile": percentile, "index_pe": 26.0, "months": 128, "as_of": "2026-09-30"})
    assert reading["state"] == state


def test_missing_inputs_are_named_not_guessed():
    regime = build_regime(overview=None, vix=16.0, vix_as_of="2026-10-16", hy_oas=None,
                          treasury={"spread_10y_2y_bp": 33.0}, valuation=None)
    assert [item["key"] for item in regime["items"]] == ["volatility", "curve"]
    assert regime["unavailable"] == ["趋势", "宽度", "参与度", "信用", "估值"]


@pytest.mark.asyncio
async def test_collect_market_regime_reads_each_source(monkeypatch):
    async def overview(db, universe, period):
        return _overview([100 + day * 0.1 for day in range(252)], breadth=[55.0] * 6, ratio=[100.0] * 22)

    async def hy_oas(now=None):
        return {"value_pct": 3.23, "change_1m_pt": 0.07, "percentile": 12.0, "as_of": "2026-10-15",
                "series": "BAMLH0A0HYM2", "source_url": "https://fred.stlouisfed.org/series/BAMLH0A0HYM2"}

    async def valuation():
        return {"percentile": 91.0, "index_pe": 25.97, "months": 128, "as_of": "2026-09-30"}

    monkeypatch.setattr(market_regime, "get_market_overview", overview)
    monkeypatch.setattr(market_regime, "get_hy_oas", hy_oas)
    monkeypatch.setattr(market_regime, "valuation_summary", valuation)
    context = {"quotes": [{"ticker": "VIX.INDX", "price": 21.4, "session_date": "2026-10-16"}],
               "treasury": {"spread_10y_2y_bp": 33.0, "as_of": "2026-10-16"}}

    regime = await market_regime.collect_market_regime(context, NOW)

    assert [(item["label"], item["state"]) for item in regime["items"]] == [
        ("趋势", "上升"), ("宽度", "中性"), ("参与度", "均衡"), ("波动", "偏高"),
        ("信用", "平稳"), ("利率曲线", "偏平"), ("估值", "偏贵"),
    ]
    assert regime["unavailable"] == [] and regime["credit_source"]["series"] == "BAMLH0A0HYM2"


@pytest.mark.asyncio
async def test_post_market_regime_reads_trend_and_participation_through_the_close(monkeypatch):
    async def overview(db, universe, period):
        return _overview([100 + day * 0.1 for day in range(252)], breadth=[55.0] * 6, ratio=[100.0] * 22)

    async def no_spread(now=None):
        raise CreditSpreadUnavailable("offline")

    async def no_valuation():
        return None

    monkeypatch.setattr(market_regime, "get_market_overview", overview)
    monkeypatch.setattr(market_regime, "get_hy_oas", no_spread)
    monkeypatch.setattr(market_regime, "valuation_summary", no_valuation)
    context = {"quotes": [_close_quote("SPY.US", 95.0, 100.0), _close_quote("RSP.US", 101.0, 100.0)]}

    regime = await market_regime.collect_market_regime(context, NOW)

    assert {item["key"]: item["as_of"] for item in regime["items"]} == {
        "trend": "2026-10-19", "breadth": "2026-10-16", "participation": "2026-10-19",
    }


# --- anomaly clusters --------------------------------------------------------


DATACENTER = ("ai-datacenter", "AI 数据中心")
CLOUD = ("cloud-software", "云软件")


def test_clusters_take_the_largest_theme_first_and_keep_directions_apart():
    moves = {"CRWV.US": -11.5, "NBIS.US": -11.6, "APLD.US": -10.8, "CIFR.US": -10.2,
             "TWLO.US": -12.2, "DOCN.US": -11.4, "OKTA.US": -10.8, "VICR.US": -12.5, "AAA.US": 9.0}
    themes = {"CRWV.US": [DATACENTER], "NBIS.US": [DATACENTER, CLOUD], "APLD.US": [DATACENTER],
              "CIFR.US": [DATACENTER], "TWLO.US": [CLOUD], "DOCN.US": [CLOUD, DATACENTER], "OKTA.US": [CLOUD]}
    industries = {"TWLO.US": "Software", "OKTA.US": "Software", "VICR.US": "Electrical"}

    clusters = cluster_anomalies(moves, themes, industries)

    assert [(item["name"], item["tickers"]) for item in clusters] == [
        ("AI 数据中心", ["NBIS.US", "CRWV.US", "DOCN.US", "APLD.US", "CIFR.US"]),
    ]
    assert clusters[0]["direction"] == "down"
    assert clusters[0]["average_move"] == pytest.approx(-11.1)


def test_leftover_movers_group_by_industry():
    moves = {"A.US": 8.0, "B.US": 9.5, "C.US": 7.1, "D.US": -6.0}
    industries = {"A.US": "Semiconductors", "B.US": "Semiconductors", "C.US": "Semiconductors", "D.US": "Semiconductors"}

    clusters = cluster_anomalies(moves, {}, industries)

    assert clusters == [{
        "kind": "industry", "name": "半导体", "slug": None, "direction": "up",
        "tickers": ["B.US", "A.US", "C.US"], "average_move": 8.2, "industry": "Semiconductors",
    }]


@pytest.mark.asyncio
async def test_collect_clusters_reads_active_members_and_screener_industries(db_session):
    as_of = date(2026, 10, 16)
    theme = Theme(slug="ai-datacenter", name_en="AI data centres", name_zh="AI 数据中心", definition="d",
                  keywords=[], negative_keywords=[], proxy_etfs=[], seed_core=[], pins=[], bans=[],
                  status="active", taxonomy_version="v1")
    retired = Theme(slug="old", name_en="Old", name_zh="旧题材", definition="d", keywords=[], negative_keywords=[],
                    proxy_etfs=[], seed_core=[], pins=[], bans=[], status="retired", taxonomy_version="v1")
    db_session.add_all([theme, retired])
    await db_session.flush()

    def member(theme_id, ticker, valid_to=None):
        return ThemeMember(theme_id=theme_id, ticker=ticker, tier="core", weight=1.0, exposure=3, sources=[],
                           review_status="verified", valid_from=date(2026, 1, 1), valid_to=valid_to)

    db_session.add_all([
        member(theme.id, "CRWV.US"), member(theme.id, "NBIS.US"),
        member(theme.id, "APLD.US", valid_to=date(2026, 10, 1)),  # left the theme before the scan
        member(retired.id, "APLD.US"), member(retired.id, "CIFR.US"), member(retired.id, "RIOT.US"),
        *(StockScreenerSnapshot(ticker=ticker, date=as_of, industry="Software") for ticker in ("TWLO.US", "DOCN.US", "OKTA.US")),
    ])
    await db_session.commit()
    anomalies = [{"ticker": ticker, "price_change": move} for ticker, move in (
        ("CRWV.US", -11.5), ("NBIS.US", -11.6), ("APLD.US", -10.8), ("CIFR.US", -10.2), ("RIOT.US", -10.8),
        ("TWLO.US", -12.2), ("DOCN.US", -11.4), ("OKTA.US", -10.8), ("XYZ.US", None),
    )]

    evidence = await collect_anomaly_clusters(anomalies, as_of)

    assert evidence["movers"] == 8
    assert [(item["kind"], item["name"], len(item["tickers"])) for item in evidence["clusters"]] == [
        ("industry", "软件", 3),
    ]


# --- report wiring -------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_attaches_regime_and_clusters_only_when_enabled(monkeypatch):
    async def regime(context, now):
        return {"items": [{"label": "趋势", "state": "上升"}], "unavailable": []}

    async def clusters(anomalies, as_of):
        return {"clusters": [{"name": "AI 数据中心"}], "as_of": as_of.isoformat()}

    monkeypatch.setattr(daily_reporter, "collect_market_regime", regime)
    monkeypatch.setattr(daily_reporter, "collect_anomaly_clusters", clusters)
    monkeypatch.setattr(daily_reporter.settings, "DAILY_REPORT_REGIME_ENABLED", True)
    context = {"report_date": "2026-10-16"}
    await daily_reporter._attach_regime_and_clusters(context, [{"ticker": "A.US", "price_change": -5}], NOW)
    assert context["regime"]["items"][0]["state"] == "上升"
    assert context["anomaly_clusters"]["as_of"] == "2026-10-16"

    monkeypatch.setattr(daily_reporter.settings, "DAILY_REPORT_REGIME_ENABLED", False)
    monkeypatch.setattr(daily_reporter.settings, "DAILY_REPORT_ANOMALY_CLUSTERS_ENABLED", False)
    quiet = {"report_date": "2026-10-16"}
    await daily_reporter._attach_regime_and_clusters(quiet, [{"ticker": "A.US", "price_change": -5}], NOW)
    assert "regime" not in quiet and "anomaly_clusters" not in quiet

    async def broken(context, now):
        raise RuntimeError("boom")

    monkeypatch.setattr(daily_reporter, "collect_market_regime", broken)
    monkeypatch.setattr(daily_reporter.settings, "DAILY_REPORT_REGIME_ENABLED", True)
    failed = {"report_date": "2026-10-16"}
    await daily_reporter._attach_regime_and_clusters(failed, [], NOW)
    assert failed["warnings"] == ["市场状态暂不可用。"]


def test_every_screener_industry_has_a_chinese_name():
    from services.industry_names import INDUSTRY_ZH, industry_zh

    assert industry_zh("Software - Infrastructure") == "基础软件"
    assert industry_zh("Brand New Industry") == "Brand New Industry"
    assert industry_zh(None) is None
    # Names are unique so two industries never read as one group.
    assert len(set(INDUSTRY_ZH.values())) == len(INDUSTRY_ZH)
