from datetime import date, datetime, timedelta, timezone
import json
import math

import httpx
import pytest
from sqlalchemy import select

from models import DataPublication, PipelineRun, Theme, ThemeDaily
from services import eodhd_client, report_market as market
from services.daily_reporter import render_daily_report
from services.report_renderer import grouped_events, morning_comparison, quality_summary, quote_line, render_events_and_quality, render_market_sections


NOW = datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc)  # Monday 10:00 EDT
SPY = market.MARKET_INSTRUMENTS[0]


@pytest.mark.parametrize("observed,display", [("2026-09-14", True), ("2026-09-07", True),
    ("2026-09-06", False), ("2026-08-06", False), ("2026-09-15", False), (None, False), ("invalid", False)])
def test_old_or_unverifiable_breadth_is_not_repeated_as_useful_context(observed, display):
    breadth = {"as_of": observed, "universe": "S&P 500", "advances": 321, "declines": 100, "unchanged": 10,
               "coverage_pct": 99, "pct_above_ma20": 80, "pct_above_ma50": 70, "pct_above_ma200": 60}
    context = {"report_date": "2026-09-14", "quotes": [], "breadth": breadth}
    detail = "\n".join(render_market_sections(context, "morning_briefing"))
    notice = "\n".join(quality_summary(context))
    assert ("涨 321" in detail) is display
    assert ("不展示" in notice) is not display
    assert context["breadth"] == breadth


def test_watchlist_ticker_is_not_repeated_as_its_own_name():
    row = {"name": "MELI.US", "ticker": "MELI.US", "price": None}
    assert quote_line(row, "morning_briefing") == "- MELI.US：数据不可用"


def quote(asset=SPY, *, when=NOW - timedelta(minutes=15), close=102, previous=100, opens=101):
    return {"code": asset.ticker, "timestamp": when.timestamp(), "close": close,
            "previousClose": previous, "open": opens}


@pytest.mark.parametrize("invalid", [None, "NA", "nan", float("inf"), 0, -1, True])
def test_invalid_prices_are_missing_not_zero_moves(invalid):
    row = market.normalize_quote(SPY, quote(close=invalid), NOW)
    assert row["price"] is None
    assert row["change_pct"] is None
    assert not row["fresh"]


def test_quote_returns_and_intraday_freshness():
    row = market.normalize_quote(SPY, quote(), NOW)
    assert row["fresh"]
    assert row["change_pct"] == 2
    assert row["from_open_pct"] == pytest.approx(0.9901)
    assert row["session"]["state"] == "盘中"
    assert row["session_date"] == "2026-09-14"
    assert row["status_label"] == "延迟报价"


def test_wrong_symbol_or_future_quote_is_rejected():
    assert market.normalize_quote(SPY, {**quote(), "code": "QQQ.US"}, NOW)["price"] is None
    assert market.normalize_quote(SPY, quote(when=NOW + timedelta(hours=1)), NOW)["price"] is None


def test_missing_previous_close_does_not_invent_return():
    row = market.normalize_quote(SPY, quote(previous="NA"), NOW)
    assert row["price"] == 102
    assert row["change_pct"] is None


def test_opening_delay_does_not_treat_previous_session_as_today():
    row = market.normalize_quote(SPY, quote(when=datetime(2026, 9, 11, 20, tzinfo=timezone.utc)), NOW)
    assert row["status"] == "stale"
    assert not row["fresh"]
    early = datetime(2026, 9, 14, 13, 35, tzinfo=timezone.utc)
    assert not market.normalize_quote(SPY, quote(when=early - timedelta(minutes=20)), early)["fresh"]


def test_overseas_close_is_fresh_despite_elapsed_hours():
    asset = next(asset for asset in market.MARKET_INSTRUMENTS if asset.ticker == "HSI.INDX")
    row = market.normalize_quote(asset, quote(asset, when=datetime(2026, 9, 14, 8, tzinfo=timezone.utc)), NOW)
    assert row["fresh"]
    assert row["session"]["state"] == "已收盘"
    assert row["session_date"] == "2026-09-14"


def test_us_holiday_dst_and_early_close():
    holiday = market.session_context(SPY, datetime(2026, 9, 7, 15, tzinfo=timezone.utc))
    assert holiday["state"] == "休市"
    assert holiday["expected_date"] == "2026-09-04"
    winter = market.session_context(SPY, datetime(2026, 1, 5, 15, 30, tzinfo=timezone.utc))
    assert winter["open"].startswith("2026-01-05T14:30")
    shortened = market.session_context(SPY, datetime(2026, 11, 27, 18, 30, tzinfo=timezone.utc))
    assert shortened["state"] == "已收盘"
    assert shortened["close"].startswith("2026-11-27T18:00")


def test_all_default_calendars_cover_today_including_2026_china():
    for asset in market.MARKET_INSTRUMENTS:
        assert market.session_context(asset, NOW)["expected_date"] is not None
    china = market.session_context(market.Instrument("CSI300.INDX", "沪深300", "equity", "CNY", "XSHG"), NOW)
    assert china["expected_date"] == "2026-09-14"
    tokyo = market.session_context(market.Instrument("N225.INDX", "日经225", "equity", "JPY", "XTKS"), NOW.replace(hour=6, minute=15))
    assert tokyo["state"] == "盘中"
    assert tokyo["close"].startswith("2026-09-14T06:30")


def test_calendar_failure_is_contained_and_never_marks_quotes_current(monkeypatch):
    def unavailable(*args):
        raise ValueError("calendar out of range")
    monkeypatch.setattr(market, "_schedule", unavailable)
    result = market.normalize_quote(SPY, quote(), NOW)
    assert result["price"] == 102
    assert not result["fresh"]
    assert result["status_label"] == "交易日无法核验"


def test_before_close_quote_is_not_final_close():
    now = datetime(2026, 9, 14, 20, 5, tzinfo=timezone.utc)
    row = market.normalize_quote(SPY, quote(when=now - timedelta(minutes=20)), now)
    assert row["status"] == "partial"
    assert not row["fresh"]


def test_eod_fallback_is_dated_and_split_adjusted():
    rows = [
        {"date": "2026-09-10", "close": 200, "adjusted_close": 100},
        {"date": "2026-09-11", "close": 102, "adjusted_close": 102, "open": 101},
        {"date": "2026-09-14", "close": 110, "adjusted_close": 110},
        {"date": "2026-09-15", "close": 120, "adjusted_close": 120},
    ]
    row = market.normalize_eod(SPY, rows, NOW)
    assert row["session_date"] == "2026-09-11"  # Never use today's incomplete daily bar.
    assert row["change_pct"] == 2
    assert row["basis"] == "eod_adjusted"
    assert not row["fresh"]
    after_close = market.normalize_eod(SPY, rows, NOW.replace(hour=21))
    assert after_close["session_date"] == "2026-09-14"
    assert after_close["fresh"]


def test_rates_use_basis_points_and_preserve_dates():
    result = market.normalize_rates({"observations": [
        {"date": "2026-09-10", "yields": {"2y": 4.10, "10y": 4.30, "30y": 4.9}},
        {"date": "2026-09-11", "yields": {"2y": 4.18, "10y": 4.27, "30y": None}},
        {"date": "2026-09-15", "yields": {"2y": 99, "10y": 99}},
    ], "meta": {}}, NOW)
    assert result["values"][0]["change_bp"] == 8
    assert result["values"][1]["change_bp"] == -3
    assert result["values"][2]["change_bp"] is None
    assert result["spread_10y_2y_bp"] == 9
    assert result["spread_change_bp"] == -11
    assert result["as_of"] == "2026-09-11"


def test_continuous_market_does_not_use_unfinished_daily_bar():
    btc = next(asset for asset in market.MARKET_INSTRUMENTS if asset.ticker == "BTC-USD.CC")
    result = market.normalize_eod(btc, [{"date": "2026-09-13", "close": 100}, {"date": "2026-09-14", "close": 110}], NOW)
    assert result["session_date"] == "2026-09-13"
    assert not result["fresh"]


def test_fx_staleness_is_stricter_than_equities():
    fx = next(asset for asset in market.MARKET_INSTRUMENTS if asset.ticker == "USDJPY.FOREX")
    assert not market.normalize_quote(fx, quote(fx), NOW)["fresh"]
    assert market.normalize_quote(fx, quote(fx, when=NOW - timedelta(minutes=1)), NOW)["fresh"]


@pytest.mark.asyncio
async def test_core_configuration_and_watchlist_are_bounded_and_deduplicated(monkeypatch):
    monkeypatch.setattr(market.settings, "DAILY_REPORT_CORE_SYMBOLS", "NVDA.US,0700.HK,NVDA.US,INVALID,AAA.OTHER")
    monkeypatch.setattr(market.settings, "DAILY_REPORT_WATCHLIST_LIMIT", 1)
    async def watched(db):
        return ["NVDA.US", "AAPL.US", "MSFT.US"]
    monkeypatch.setattr(market, "get_watchlist", watched)
    assets, warnings = await market.report_instruments()
    core = [asset.ticker for asset in assets if asset.group == "core"]
    assert core == ["NVDA.US", "0700.HK", "AAPL.US"]
    assert any("上限" in text or "前1只" in text for text in warnings)
    assert len(warnings) == 3


def test_scheduler_uses_configured_new_york_times(monkeypatch):
    from core import scheduler
    calls = {}
    class FakeScheduler:
        running = True
        def add_job(self, job, trigger, **kwargs):
            calls[kwargs["id"]] = kwargs
    monkeypatch.setattr(scheduler, "scheduler", FakeScheduler())
    monkeypatch.setattr(scheduler.settings, "DAILY_REPORT_MORNING_HOUR", 10)
    monkeypatch.setattr(scheduler.settings, "DAILY_REPORT_MORNING_MINUTE", 0)
    monkeypatch.setattr(scheduler.settings, "DAILY_REPORT_CLOSE_HOUR", 16)
    monkeypatch.setattr(scheduler.settings, "DAILY_REPORT_CLOSE_MINUTE", 30)
    scheduler.start_scheduler()
    assert (calls["morning_briefing"]["hour"], calls["morning_briefing"]["minute"]) == (10, 0)
    assert (calls["post_market_summary"]["hour"], calls["post_market_summary"]["minute"]) == (16, 30)


def test_no_stock_anomalies_still_renders_markets_and_missing_data():
    context = {"report_date": "2026-09-14", "captured_at": NOW.isoformat(),
               "quotes": [market.normalize_quote(SPY, quote(), NOW)], "events": {"disabled": True}}
    content = render_daily_report([], report_type="morning_briefing", market_context=context)
    assert "全球股票市场" in content and "SPY ETF" in content
    assert "+2.00%" in content and "延迟报价" in content
    assert "当期可用 1/1" in content
    assert "今日市场平稳收盘" not in content
    assert "美债曲线暂不可用" in content


def test_stale_quotes_are_excluded_from_highlights():
    row = market.normalize_quote(SPY, quote(when=NOW - timedelta(days=4), close=999), NOW)
    lines = render_market_sections({"quotes": [row]}, "morning_briefing")
    assert "当前交易日有效报价不足" in lines[2]
    assert "899.00%" not in "\n".join(lines[:5])


@pytest.mark.asyncio
async def test_default_pool_includes_four_metals_and_one_btc_without_duplicates(monkeypatch):
    monkeypatch.setattr(market.settings, "DAILY_REPORT_INCLUDE_WATCHLIST", False)
    monkeypatch.setattr(market.settings, "DAILY_REPORT_CORE_SYMBOLS", "AAPL.US,MSFT.US,NVDA.US,AMZN.US,0700.HK,9988.HK,2330.TW,ASML.AS")
    assets, warnings = await market.report_instruments()
    tickers = [asset.ticker for asset in assets]
    assert len(tickers) == len(set(tickers)) == 45
    assert {"GLD.US", "SLV.US", "PPLT.US", "PALL.US", "BTC-USD.CC"} <= set(tickers)
    assert not warnings
    assert all(asset.proxy and asset.calendar == "XNYS" for asset in market.PRECIOUS_METALS)


def test_metals_and_btc_are_visible_in_summary_and_separate_details():
    focus = [asset for asset in market.MARKET_INSTRUMENTS if asset.ticker in {*market.PRECIOUS_METAL_TICKERS, "BTC-USD.CC", "ETH-USD.CC"}]
    rows = [market.normalize_quote(asset, quote(asset, when=NOW - timedelta(minutes=1)), NOW) for asset in focus]
    content = "\n".join(render_market_sections({"quotes": rows}, "morning_briefing"))
    summary = content.split("\n\n**", 1)[0]
    for label in ("黄金 +2.00%", "白银 +2.00%", "铂金 +2.00%", "钯金 +2.00%", "BTC 比特币 102.00 USD / +2.00%"):
        assert label in summary
    assert "非24小时涨跌" in summary
    metals = content.split("**贵金属（ETF代理）**")[1].split("\n\n**", 1)[0]
    assert all(asset.ticker in metals for asset in market.PRECIOUS_METALS)
    crypto = content.split("**加密资产（BTC / ETH）**")[1].split("**美债收益率（日频）**")[0]
    assert "BTC-USD.CC" in crypto and "ETH-USD.CC" in crypto
    assert "BTC-USD.CC" not in metals


def test_focus_summary_discloses_stale_missing_and_unknown_moves():
    rows = [
        {"ticker": "GLD.US", "name": "黄金", "price": 999, "change_pct": 899, "fresh": False},
        {"ticker": "SLV.US", "name": "白银", "price": None, "fresh": False},
        {"ticker": "BTC-USD.CC", "name": "比特币", "price": 100, "fresh": True, "change_pct": None},
    ]
    summary = "\n".join(render_market_sections({"quotes": rows}, "morning_briefing")).split("\n\n**", 1)[0]
    assert "黄金 历史／待更新" in summary and "899.00%" not in summary
    assert "白银 数据不可用" in summary and "铂金 未纳入快照" in summary
    assert "BTC 比特币 100.00 USD / 涨跌未知" in summary


def test_open_return_is_not_shown_for_prior_day_or_non_equity_quotes():
    before_open = NOW.replace(hour=12)
    yesterday = datetime(2026, 9, 11, 20, tzinfo=timezone.utc)
    row = market.normalize_quote(SPY, quote(when=yesterday), before_open)
    text = quote_line(row, "morning_briefing", "2026-09-14")
    assert "开盘以来" not in text and "前一交易日" in text
    assert "09-11 20:00 UTC" in text
    current = market.normalize_quote(SPY, quote(), NOW)
    assert "开盘以来" in quote_line(current, "morning_briefing", "2026-09-14")
    assert "开盘以来" not in quote_line(current, "post_market_summary", "2026-09-14")
    assert "开盘以来" not in quote_line({**current, "group": "commodity"}, "morning_briefing", "2026-09-14")


def test_missing_modules_and_warnings_appear_once_without_empty_sections():
    context = {"report_date": "2026-09-14", "quotes": [], "events": None,
               "warnings": ["市场宽度暂不可用。", "市场宽度暂不可用。", "验收注入过期"]}
    report = render_daily_report([], report_type="morning_briefing", market_context=context)
    assert report.count("市场宽度暂不可用") == 1
    assert report.count("验收注入过期") == 1
    assert report.count("当期可用") == 1
    assert "**美债收益率（日频）**" not in report
    assert "**今明交易日关注**" not in report
    assert "**贵金属（ETF代理）**" not in report
    assert "事件日历暂不可用" in report


def test_calendar_groups_variants_keeps_zero_and_important_non_numeric_events():
    base = {"kind": "economic", "date": "2026-09-16 18:00:00", "actual": None, "estimate": None}
    rows = [{**base, "name": "Interest Rate Projection - Current"},
            {**base, "name": "Interest Rate Projection - 1st Yr", "estimate": 4},
            {**base, "name": "Fed Interest Rate Decision", "actual": 0, "estimate": 0},
            {**base, "name": "Fed Press Conference"},
            {**base, "name": "Retail Sales", "period": "Aug", "comparison": "yoy", "estimate": 4.7},
            {**base, "name": "Retail Sales", "period": "Aug", "comparison": "mom", "estimate": 0.8}]
    lines = grouped_events([*rows, rows[0]])
    text = "\n".join(lines)
    assert len(lines) == 4 and text.count("美联储利率预测") == 1
    assert "Current；1st Yr 预期 4.00" in text
    assert "实际 0.00 / 预期 0.00" in text
    assert "Fed Press Conference。" in text and "实际 —" not in text and "预期 —" not in text
    assert "yoy 预期 4.70；mom 预期 0.80" in text
    # Different dates/periods must not collapse together.
    assert len(grouped_events([rows[4], {**rows[4], "period": "Sep"}, {**rows[4], "date": "2026-09-17 18:00:00"}])) == 3


@pytest.mark.asyncio
async def test_calendar_keeps_evidence_before_grouping_and_display_limit(monkeypatch):
    async def calendar(kind, *args, **kwargs):
        if kind == "earnings":
            return []
        return [{"country": "US", "type": "Interest Rate Projection - Current", "date": "2026-09-14 18:00:00"}] * 14 + [
            {"country": "US", "type": "Initial Jobless Claims", "date": "2026-09-15 12:30:00"}]
    monkeypatch.setattr(eodhd_client, "get_report_calendar", calendar)
    events = await market.collect_events([], NOW)
    assert len(events["items"]) == events["total"] == 15
    text = "\n".join(render_events_and_quality({"events": events}))
    assert "Initial Jobless Claims" in text
    assert "合并为 2 组" in text


@pytest.mark.parametrize("ticker", ["SLV.US", "PPLT.US", "PALL.US", "BTC-USD.CC"])
def test_focus_assets_support_same_session_morning_comparison(ticker):
    asset = next(asset for asset in market.MARKET_INSTRUMENTS if asset.ticker == ticker)
    morning = {"report_date": "2026-09-14", "quotes": [market.normalize_quote(asset, quote(asset, when=NOW, close=100), NOW)]}
    later = NOW + timedelta(hours=1)
    current = {"report_date": "2026-09-14", "quotes": [market.normalize_quote(asset, quote(asset, when=later, close=103), later)]}
    assert "+3.00%" in morning_comparison(current, morning)[0]


def test_morning_comparison_needs_same_date_fresh_newer_quotes():
    morning = {"report_date": "2026-09-14", "quotes": [market.normalize_quote(SPY, quote(close=100), NOW)]}
    late = NOW.replace(hour=20, minute=30)
    current = {"report_date": "2026-09-14", "quotes": [market.normalize_quote(SPY, quote(when=late - timedelta(minutes=30), close=102), late)]}
    assert "+2.00%" in morning_comparison(current, morning)[0]
    assert "无法比较" in morning_comparison(current, {**morning, "report_date": "2026-09-11"})[0]
    current["quotes"][0]["fresh"] = False
    assert "暂无法" in morning_comparison(current, morning)[0] or "暂无" in morning_comparison(current, morning)[0]


@pytest.mark.asyncio
async def test_quotes_fall_back_without_substituting_another_symbol(monkeypatch):
    async def fake_live(tickers, client=None):
        return [{**quote(), "code": "WRONG.US"}]
    async def fake_eod(ticker, from_date, to_date, client=None):
        assert ticker == "SPY.US"
        return [{"date": "2026-09-11", "close": 100}, {"date": "2026-09-14", "close": 102}]
    monkeypatch.setattr(eodhd_client, "get_live_quotes", fake_live)
    monkeypatch.setattr(eodhd_client, "get_eod_historical_data", fake_eod)
    rows = await market.collect_quotes([SPY], NOW.replace(hour=21))
    assert rows[0]["basis"] == "eod_close"
    assert rows[0]["change_pct"] == 2


@pytest.mark.asyncio
async def test_collector_isolates_unavailable_components(monkeypatch):
    async def assets():
        return [SPY], []
    async def quotes(assets, now):
        return [market.normalize_quote(SPY, quote(), now)]
    async def failed(*args, **kwargs):
        raise RuntimeError("upstream unavailable")
    monkeypatch.setattr(market, "report_instruments", assets)
    monkeypatch.setattr(market, "collect_quotes", quotes)
    monkeypatch.setattr(market, "get_yield_curve", failed)
    monkeypatch.setattr(market, "collect_breadth", failed)
    monkeypatch.setattr(market, "collect_events", failed)
    result = await market.collect_market_context(now=NOW)
    assert result["quotes"][0]["fresh"]
    assert result["treasury"] is None
    assert "美债曲线暂不可用。" in result["warnings"]


@pytest.mark.asyncio
async def test_events_filter_symbols_and_keep_unconfirmed_times(monkeypatch):
    async def fake_calendar(kind, from_date, to_date, client=None):
        if kind == "economic":
            return [{"country": "US", "type": "Inflation Rate", "date": "2026-09-15 12:30:00", "actual": None, "estimate": 3.1},
                    {"country": "JP", "type": "Interest Rate", "date": "2026-09-15 12:30:00"}]
        return [{"code": "NVDA.US", "report_date": "2026-09-15", "before_after_market": "AfterMarket"},
                {"code": "OTHER.US", "report_date": "2026-09-15"}]
    monkeypatch.setattr(eodhd_client, "get_report_calendar", fake_calendar)
    result = await market.collect_events([market.Instrument("NVDA.US", "英伟达", "core")], NOW)
    assert result["total"] == 2
    assert "未提供时区" in result["timezone_note"]
    assert result["items"][1]["actual"] is None


@pytest.mark.asyncio
async def test_named_quote_api_batches_and_economic_path(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path == "/api/economic-events":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[quote()])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await eodhd_client.get_live_quotes(["SPY.US", "QQQ.US", "SPY.US"], client=client)
        assert rows[0]["code"] == "SPY.US"
        assert calls[0].url.params["s"] == "QQQ.US"
        assert await eodhd_client.get_report_calendar("economic", "2026-09-14", "2026-09-15", client=client) == []
        assert calls[1].url.path == "/api/economic-events"


# ---------------------------------------------------------------------------
# 题材热度 (theme heat, morning report only)
# ---------------------------------------------------------------------------


def theme_heat(as_of="2026-09-11", **values):
    heat = {"as_of": as_of, "expected_as_of": "2026-09-11", "pipeline_run_id": 7, "scoring_version": "theme-heat-v1",
            "ranked": 12, "degraded": False, "pit": True,
            "top": [{"slug": "memory-storage", "name_zh": "存储芯片", "rank": 1, "rank_change": 2, "stage": "accelerating",
                     "stage_prev": "neutral", "heat": 85.04, "excess_21d_pct": 8.42, "b50_pct": 72.0,
                     "leaders": ["MU.US", "WDC.US"]},
                    {"slug": "quantum", "name_zh": "量子计算", "rank": 2, "rank_change": -1, "stage": "crowded",
                     "stage_prev": "leading", "heat": 80.0, "excess_21d_pct": -1.5, "b50_pct": None, "leaders": []}],
            "emerging": [{"slug": "ai-power", "name_zh": "AI电力", "rank": 6}],
            "stage_changes": [{"slug": "ai-power", "name_zh": "AI电力", "rank": 6, "from": "neutral", "to": "emerging"},
                              {"slug": "quantum", "name_zh": "量子计算", "rank": 2, "from": "leading", "to": "crowded"}]}
    heat.update(values)
    return heat


@pytest.mark.parametrize("observed,display", [("2026-09-11", True), ("2026-09-07", True),
    ("2026-09-06", False), ("2026-09-15", False), (None, False), ("invalid", False)])
def test_old_or_unverifiable_theme_heat_is_not_shown(observed, display):
    heat = theme_heat(observed)
    context = {"report_date": "2026-09-14", "quotes": [], "theme_heat": heat}
    detail = "\n".join(render_market_sections(context, "morning_briefing"))
    notice = "\n".join(quality_summary(context))
    assert ("**题材热度**" in detail) is display
    assert ("题材热度" in notice and "不展示" in notice) is not display
    assert context["theme_heat"] == theme_heat(observed)


def test_theme_section_renders_top_rows_events_and_notes_after_breadth():
    breadth = {"as_of": "2026-09-11", "universe": "S&P 500", "advances": 321, "declines": 100, "unchanged": 10,
               "coverage_pct": 99, "pct_above_ma20": 80, "pct_above_ma50": 70, "pct_above_ma200": 60}
    core = {"ticker": "AAPL.US", "name": "苹果", "group": "core", "price": None}
    context = {"report_date": "2026-09-14", "quotes": [core], "breadth": breadth,
               "theme_heat": theme_heat("2026-09-10", degraded=True, pit=False)}
    lines = render_market_sections(context, "morning_briefing")
    text = "\n".join(lines)
    assert text.index("市场宽度：") < text.index("**题材热度**") < text.index("**核心资产与自选股**")
    assert "截至 2026-09-10 的已发布题材数据；已排名 12 个题材" in text
    assert "- 题材数据截至 2026-09-10，最新交易日 2026-09-11 尚未发布。" in text
    assert ("- #1 存储芯片（加速）：热度 85.0，排名 ↑2；21日超额 +8.42%；MA50以上 72.0%；龙头 MU、WDC") in text
    assert "- #2 量子计算（拥挤）：热度 80.0，排名 ↓1；21日超额 -1.50%；MA50以上 —；龙头 —" in text
    assert "- 当日萌芽：AI电力。" in text
    assert "- 阶段变化：量子计算 主线→拥挤。" in text
    assert "降级" in text and "非时点成员" in text
    assert lines[2].startswith("- 美股基准ETF")


def test_theme_section_is_morning_only_and_gap_only_when_collected():
    context = {"report_date": "2026-09-14", "quotes": [], "theme_heat": theme_heat()}
    assert "**题材热度**" not in "\n".join(render_market_sections(context, "post_market_summary"))
    legacy = {"report_date": "2026-09-14", "quotes": [], "warnings": []}
    assert "题材热度" not in "\n".join(quality_summary(legacy))
    assert "题材热度" not in render_daily_report([], report_type="morning_briefing", market_context=legacy)
    missing = {**legacy, "theme_heat": None, "warnings": ["题材热度暂不可用。"]}
    report = render_daily_report([], report_type="morning_briefing", market_context=missing)
    assert report.count("题材热度暂不可用") == 1
    assert "**题材热度**" not in report
    empty = {**legacy, "theme_heat": theme_heat(top=[], emerging=[], stage_changes=[], ranked=0)}
    assert "- 本期没有可排名的题材。" in "\n".join(render_market_sections(empty, "morning_briefing"))


async def _seed_theme_publication(db, as_of, *, status="published", publish=True, slug="memory-storage", **values):
    theme = await db.scalar(select(Theme).where(Theme.slug == slug))
    if theme is None:
        theme = Theme(slug=slug, name_en=slug, name_zh=values.pop("name_zh", "存储芯片"), definition="d", keywords=[],
                      negative_keywords=[], proxy_etfs=[], seed_core=[], pins=[], bans=[],
                      status=values.pop("theme_status", "active"), taxonomy_version="themes-v1")
        db.add(theme)
    run = PipelineRun(pipeline_name="theme_trends", target_date=as_of, status=status, stage=status,
                      version="theme-heat-v1", quality_report=values.pop("quality_report", None))
    db.add(run)
    await db.flush()
    if publish:
        db.add(DataPublication(dataset="theme_trends", as_of_date=as_of, pipeline_run_id=run.id, status="published"))
    row = {"scoring_version": "theme-heat-v1", "pit": True, "rankable": True, "stage": "accelerating",
           "candidate_stage": "accelerating", "stage_prev": "neutral", "stage_changed": True, "heat_now": 85.04,
           "rank_now": 1, "rank_change": 2, "x21": math.log(1.0842), "b50": 0.72, "leaders": ["MU.US", "WDC.US"]}
    row.update(values)
    db.add(ThemeDaily(source_run_id=run.id, theme_id=theme.id, date=as_of, **row))
    await db.commit()
    return run


@pytest.mark.asyncio
async def test_theme_heat_reads_only_the_latest_published_run_on_or_before_report_date(db_session):
    with pytest.raises(ValueError, match="No published theme trends"):
        await market.collect_theme_heat(NOW)
    published = await _seed_theme_publication(db_session, date(2026, 9, 10), quality_report={"metrics": {"attention_degraded": True}})
    await _seed_theme_publication(db_session, date(2026, 9, 11), status="failed", heat_now=10.0)
    await _seed_theme_publication(db_session, date(2026, 9, 11), publish=False, heat_now=20.0)
    await _seed_theme_publication(db_session, date(2026, 9, 15), heat_now=30.0)  # after the report date
    retired = Theme(slug="old-theme", name_en="Old", name_zh="旧题材", definition="d", keywords=[], negative_keywords=[],
                    proxy_etfs=[], seed_core=[], pins=[], bans=[], status="retired", taxonomy_version="themes-v1")
    db_session.add(retired)
    await db_session.flush()
    db_session.add(ThemeDaily(source_run_id=published.id, theme_id=retired.id, date=date(2026, 9, 10),
                              scoring_version="theme-heat-v1", stage="neutral", candidate_stage="neutral",
                              rankable=True, rank_now=1, emerging_first_20=True))
    await db_session.commit()
    result = await market.collect_theme_heat(NOW)
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    assert result["as_of"] == "2026-09-10" and result["expected_as_of"] == "2026-09-11"
    assert result["pipeline_run_id"] == published.id
    assert (result["ranked"], result["degraded"], result["pit"]) == (1, True, True)
    assert result["top"] == [{"slug": "memory-storage", "name_zh": "存储芯片", "rank": 1, "rank_change": 2,
                              "stage": "accelerating", "stage_prev": "neutral", "heat": 85.0, "excess_21d_pct": 8.42,
                              "b50_pct": 72.0, "leaders": ["MU.US", "WDC.US"]}]
    assert result["stage_changes"] == [{"slug": "memory-storage", "name_zh": "存储芯片", "rank": 1,
                                        "from": "neutral", "to": "accelerating"}]
    assert result["emerging"] == []


@pytest.mark.asyncio
async def test_theme_heat_point_in_time_ignores_memberless_themes(db_session):
    as_of = date(2026, 9, 11)
    run = await _seed_theme_publication(db_session, as_of, n_members=8)
    # A theme synced after the last membership refresh: no basket, so scoring writes pit=False.
    fresh = Theme(slug="new-theme", name_en="New", name_zh="新题材", definition="d", keywords=[], negative_keywords=[],
                  proxy_etfs=[], seed_core=[], pins=[], bans=[], status="active", taxonomy_version="themes-v1")
    db_session.add(fresh)
    await db_session.flush()
    db_session.add(ThemeDaily(source_run_id=run.id, theme_id=fresh.id, date=as_of, scoring_version="theme-heat-v1",
                              stage="insufficient", candidate_stage="insufficient", rankable=False, pit=False,
                              n_members=0))
    await db_session.commit()
    result = await market.collect_theme_heat(NOW)
    assert (result["ranked"], result["pit"]) == (1, True)
    context = {"report_date": "2026-09-14", "quotes": [], "theme_heat": result}
    text = "\n".join(render_market_sections(context, "morning_briefing"))
    assert "**题材热度**" in text and "含回填历史" not in text

    backfilled = Theme(slug="backfilled", name_en="Backfilled", name_zh="回填题材", definition="d", keywords=[],
                       negative_keywords=[], proxy_etfs=[], seed_core=[], pins=[], bans=[], status="active",
                       taxonomy_version="themes-v1")
    db_session.add(backfilled)
    await db_session.flush()
    db_session.add(ThemeDaily(source_run_id=run.id, theme_id=backfilled.id, date=as_of, scoring_version="theme-heat-v1",
                              stage="neutral", candidate_stage="neutral", rankable=True, rank_now=2, pit=False,
                              n_members=6))
    await db_session.commit()
    assert (await market.collect_theme_heat(NOW))["pit"] is False
