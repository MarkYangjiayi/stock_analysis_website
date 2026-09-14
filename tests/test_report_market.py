from datetime import datetime, timedelta, timezone

import httpx
import pytest

from services import eodhd_client, report_market as market
from services.daily_reporter import render_daily_report
from services.report_renderer import morning_comparison, render_market_sections


NOW = datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc)  # Monday 10:00 EDT
SPY = market.MARKET_INSTRUMENTS[0]


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
