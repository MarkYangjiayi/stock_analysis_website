from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import json

import pytest
from sqlalchemy import select

from models import DailyPrice, DailyReportRun, Ticker
from services import daily_reporter, rsi_monitor, watchlist_signals, weekly_digest
from services.notifications.report_card import build_daily_card, build_weekly_card
from services.report_insights import daily_headline, weekly_headline


AS_OF = "2026-09-14T20:15:00+00:00"


def q(ticker, name, group, price, change, *, fresh=True, currency="USD", proxy=None, session_date="2026-09-14", **extra):
    return {"ticker": ticker, "name": name, "group": group, "price": price, "change_pct": change,
            "previous_close": round(price / (1 + change / 100), 4) if price and change is not None else None,
            "fresh": fresh, "currency": currency, "proxy": proxy, "session_date": session_date,
            "as_of": AS_OF, "basis": "delayed_snapshot", "status_label": "收盘附近延迟报价" if fresh else "非当前交易日报价",
            "session": {"state": "已收盘"}, **extra}


SECTORS = [("XLC.US", "通信服务", 2.13), ("XLP.US", "必需消费", 1.43), ("XLV.US", "医疗保健", 1.41),
           ("XLF.US", "金融", 0.08), ("XLY.US", "可选消费", -0.05), ("XLE.US", "能源", -0.21),
           ("XLRE.US", "房地产", -0.39), ("XLB.US", "原材料", -0.73), ("XLU.US", "公用事业", -1.13),
           ("XLK.US", "科技", -1.50), ("XLI.US", "工业", -1.57)]


def context() -> dict:
    """The 2026-09-14 replay sample, reduced to the fields the card reads."""
    quotes = [
        q("SPY.US", "标普500", "equity", 762.06, -0.29, proxy="SPY ETF"),
        q("QQQ.US", "纳斯达克100", "equity", 710.52, -0.61, proxy="QQQ ETF"),
        q("IWM.US", "罗素2000", "equity", 288.90, 0.01, proxy="IWM ETF"),
        q("HSI.INDX", "恒生指数", "equity", 24892.48, 0.35, currency="HKD"),
        q("NYICDX.INDX", "美元指数", "fx", 99.4603, 0.24, currency="点"),
        q("GLD.US", "黄金", "commodity", 394.67, -1.03, proxy="GLD ETF"),
        q("VIX.INDX", "VIX", "risk", 16.73, 5.62, currency="点"),
        q("BTC-USD.CC", "BTC 比特币", "risk", 78809.99, 2.57),
        q("RSP.US", "标普等权", "structure", 190.0, 0.18, proxy="RSP ETF"),
        *(q(ticker, name, "sector", 100.0, change, proxy="板块ETF") for ticker, name, change in SECTORS),
        q("ASML.AS", "ASML", "core", 1387.80, -6.13, currency="EUR"),
        q("NVDA.US", "英伟达", "core", 211.69, -3.02),
        q("MSFT.US", "微软", "core", 506.94, 2.28),
        q("0700.HK", "腾讯", "core", 430.60, 0.51, currency="HKD", fresh=False, session_date="2026-09-11"),
    ]
    return {
        "report_date": "2026-09-14", "captured_at": "2026-09-14T20:30:00+00:00", "quotes": quotes,
        "treasury": {"as_of": "2026-09-14", "previous_date": "2026-09-11",
                     "values": [{"tenor": "2y", "yield_pct": 4.63, "change_bp": 7.0},
                                {"tenor": "10y", "yield_pct": 4.96, "change_bp": 1.0},
                                {"tenor": "30y", "yield_pct": 5.35, "change_bp": -2.0}],
                     "spread_10y_2y_bp": 33.0, "spread_change_bp": -6.0},
        "breadth": {"as_of": "2026-09-11", "universe": "S&P 500 历史成分", "advances": 300, "declines": 200,
                    "unchanged": 3, "pct_above_ma20": 51.0, "pct_above_ma50": 55.0, "pct_above_ma200": 60.0,
                    "coverage_pct": 100.0},
        "events": {"from": "2026-09-14", "to": "2026-09-15", "economic_available": True, "earnings_available": True,
                   "items": [{"kind": "earnings", "name": "NVDA.US", "date": "2026-09-15", "session": "AfterMarket"}]},
        "watchlist_signals": {"oversold": 30, "overbought": 70, "near_extreme_pct": 1.0,
                              "items": {"NVDA.US": {"as_of": "2026-09-14", "rsi": 28.4, "close": 100.0,
                                                    "high_52w": 150.0, "low_52w": 99.5}}},
        "warnings": [],
    }


def walk(node):
    yield node
    if isinstance(node, dict):
        for child in node.values():
            yield from walk(child)
    elif isinstance(node, list):
        for child in node:
            yield from walk(child)


def test_daily_headline_describes_what_moved_without_causes():
    headline = daily_headline(context(), "post_market_summary")
    assert headline == ("美股小幅回落（标普 -0.29%）；通信服务、必需消费领涨，工业、科技领跌（防御板块居前）；"
                        "等权指数跑赢 +0.47%，大市值股拖累更明显；VIX 升至 16.7。")


def test_headline_drops_clauses_built_on_stale_quotes():
    sample = context()
    by_symbol = {row["ticker"]: row for row in sample["quotes"]}
    by_symbol["SPY.US"]["fresh"] = False
    by_symbol["VIX.INDX"]["fresh"] = False
    headline = daily_headline(sample, "morning_briefing")
    assert headline.startswith("美股开盘小幅回落（标普报价缺失）")
    assert "等权" not in headline and "VIX" not in headline
    assert "关注列表中 ASML.AS -6.13%、NVDA -3.02%" in headline
    by_symbol["SPY.US"]["session"] = {"state": "未开盘"}
    assert daily_headline(sample, "morning_briefing") == (
        "美股尚未开盘；前一交易日通信服务、必需消费领涨，工业、科技领跌（防御板块居前）。")
    card = build_daily_card(sample, [], report_type="morning_briefing", headline="H。")
    encoded = json.dumps(card, ensure_ascii=False)
    assert "板块 · 前一交易日" in encoded and "标普500 · 前日 · 旧" in encoded


def test_daily_card_puts_conclusion_numbers_and_watchlist_on_first_screen():
    sample = context()
    card = build_daily_card(sample, [], report_type="post_market_summary", headline="H。",
                            site_url="https://example.com")
    header = card["header"]
    assert header["title"]["content"] == "Quantify 美股收盘 · 9月14日 周一"
    assert "北京 09-15 04:30" in header["subtitle"]["content"] and "纽约 16:30" in header["subtitle"]["content"]
    assert header["template"] == "grey"  # |SPY| < 0.5%
    assert card["config"]["summary"]["content"] == "Quantify 美股收盘 · 9月14日 周一：H。"
    elements = card["body"]["elements"]
    assert elements[0]["columns"][0]["elements"][0]["content"] == "H。"
    grids = elements[1:3]
    assert all(grid["flex_mode"] == "bisect" and len(grid["columns"]) == 4 for grid in grids)
    encoded = json.dumps(card, ensure_ascii=False)
    assert "<font color='red'>-0.29%</font>" in encoded
    assert "10Y 美债 4.96%" in encoded and "<font color='green'>+1bp</font>" in encoded
    assert "等权相对市值加权 <font color='green'>+0.47%</font>" in encoded
    assert "**今明关注**\\n- 09-15 周二：英伟达 财报（盘后）" in encoded
    # Only the one genuinely stale row is mentioned; nothing else is missing.
    footer = next(element["content"] for element in card["body"]["elements"] if element.get("text_size") == "notation")
    assert "⚠ 1 项为旧报价（已置灰）\n" in footer and "缺失" not in footer and "暂缺" not in footer
    assert "callback" not in encoded
    buttons = [node for node in walk(card) if isinstance(node, dict) and node.get("tag") == "button"]
    assert [button["behaviors"][0]["default_url"] for button in buttons] == [
        "https://example.com/market", "https://example.com/anomalies"]


def test_tables_stay_at_root_and_panels_stay_collapsed():
    card = build_daily_card(context(), [], report_type="post_market_summary", headline="H。")
    elements = card["body"]["elements"]
    tables = [element for element in elements if element["tag"] == "table"]
    assert len(tables) == 1
    nested = [node for element in elements if element["tag"] != "table"
              for node in walk(element) if isinstance(node, dict) and node.get("tag") == "table"]
    assert nested == []
    panels = [element for element in elements if element["tag"] == "collapsible_panel"]
    assert panels and all(panel["expanded"] is False for panel in panels)
    assert not any(node.get("tag") == "button" for node in walk(card) if isinstance(node, dict))


def test_watch_table_orders_by_move_greys_stale_rows_and_adds_signal_tags():
    card = build_daily_card(context(), [], report_type="post_market_summary", headline="H。",
                            site_url="https://example.com")
    table = next(element for element in card["body"]["elements"] if element["tag"] == "table")
    rows = table["rows"]
    assert [row["symbol"].split("]")[0] for row in rows] == ["[ASML.AS", "[NVDA", "[MSFT", "[0700.HK"]
    assert rows[1]["symbol"] == "[NVDA](https://example.com/?ticker=NVDA.US) 英伟达"
    assert rows[0]["price"] == "1,387.80 EUR"
    assert rows[0]["change"] == [{"text": "-6.13%", "color": "red"}]
    assert rows[2]["change"] == [{"text": "+2.28%", "color": "green"}]
    assert rows[3]["change"] == [{"text": "+0.51% 旧", "color": "neutral"}]
    assert rows[1]["signals"] == [{"text": "RSI 28 超卖", "color": "blue"},
                                  {"text": "近52周新低", "color": "red"},
                                  {"text": "财报 09-15 盘后", "color": "violet"}]


def test_footer_reports_only_actual_gaps():
    sample = context()
    sample["treasury"] = None
    sample["quotes"][4].update(price=None, change_pct=None, fresh=False)
    anomaly = {"ticker": "TEST.US", "price_change": -5, "quote_timestamp": "2026-09-14T13:19:00Z",
               "attribution_status": "no_news"}
    rows = [daily_reporter.anomaly_card_row(anomaly, sample)]
    card = build_daily_card(sample, rows, report_type="post_market_summary", headline="H。")
    footer = card["body"]["elements"][-1]["content"]
    assert "⚠ 缺失 1 项：美元指数" in footer
    assert "暂缺：美债曲线" in footer
    assert "异动股 1/1 为非当期报价" in footer
    panel = next(element for element in card["body"]["elements"]
                 if element["tag"] == "collapsible_panel" and "个股异动" in element["header"]["title"]["content"])
    assert panel["header"]["title"]["content"] == "个股异动 · TEST -5.0%"
    assert "盘前报价" in panel["elements"][0]["content"] and "暂无相关新闻" in panel["elements"][0]["content"]


def test_anomaly_row_keeps_only_company_headlines_and_short_name():
    anomaly = {"ticker": "PYPL.US", "company_name": "PayPal Holdings Inc", "price_change": -14.25,
               "quote_timestamp": "2026-09-14T20:10:00Z", "attribution_status": "completed",
               "news": [{"title": "Market movers", "link": "https://example.com/a"},
                        {"title": "PayPal drops after bid collapse", "link": "https://example.com/b"}]}
    row = daily_reporter.anomaly_card_row(anomaly, context())
    assert row["company"] == "PayPal"
    assert row["status"] is None
    assert row["headlines"] == [("PayPal drops after bid collapse", "https://example.com/b")]


def test_audit_markdown_carries_the_headline_and_signals():
    content = daily_reporter.render_daily_report([], report_type="post_market_summary", market_context=context())
    assert "结论（规则生成）：美股小幅回落" in content
    assert "- NVDA：RSI 28 超卖；近52周新低（截至 2026-09-14）" in content
    assert "<font" not in content


async def _seed(db_session, ticker, closes, target):
    db_session.add(Ticker(ticker=ticker))
    dates = rsi_monitor._recent_us_sessions(target, len(closes))
    db_session.add_all(DailyPrice(ticker=ticker, date=day, open=close, high=close, low=close, close=close,
                                  adjusted_close=close, volume=1_000) for day, close in zip(dates, closes))
    await db_session.commit()


@pytest.mark.asyncio
async def test_price_signals_use_stored_history_and_append_only_consistent_live_close(db_session):
    target, live_day = date(2025, 7, 3), date(2025, 7, 7)
    await _seed(db_session, "UP.US", [100 + index for index in range(260)], target)
    await _seed(db_session, "SPLIT.US", [100 + index for index in range(260)], target)
    live = {"UP.US": {"price": 400.0, "previous_close": 359.0},
            "SPLIT.US": {"price": 90.0, "previous_close": 89.75}}  # 4:1 split since the stored close.
    evidence = await watchlist_signals.price_signals(["UP.US", "SPLIT.US", "0700.HK"], target,
                                                     live_quotes=live, live_date=live_day)
    up, split = evidence["items"]["UP.US"], evidence["items"]["SPLIT.US"]
    assert up["includes_live_quote"] is True and up["as_of"] == "2025-07-07" and up["close"] == 400.0
    assert split["includes_live_quote"] is False and split["as_of"] == "2025-07-03"
    assert "0700.HK" not in evidence["items"]  # Non-US listings are not tagged.
    tags = watchlist_signals.signal_tags("UP.US", evidence)
    assert {"text": "RSI 100 超买", "color": "orange"} in tags
    assert {"text": "创52周新高", "color": "green"} in tags


@pytest.mark.asyncio
async def test_short_history_gets_rsi_but_no_52_week_tag(db_session):
    target = date(2025, 7, 3)
    await _seed(db_session, "NEW.US", [100 - index * 0.5 for index in range(30)], target)
    evidence = await watchlist_signals.price_signals(["NEW.US"], target)
    item = evidence["items"]["NEW.US"]
    assert item["rsi"] == 0.0 and "high_52w" not in item
    assert watchlist_signals.signal_tags("NEW.US", evidence) == [{"text": "RSI 0 超卖", "color": "blue"}]


@pytest.mark.parametrize("week_end,start,previous", [
    (date(2025, 7, 3), date(2025, 6, 30), date(2025, 6, 27)),   # Independence Day Friday.
    (date(2025, 9, 5), date(2025, 9, 2), date(2025, 8, 29)),    # Labor Day Monday.
])
def test_week_window_follows_the_nyse_calendar(week_end, start, previous):
    assert weekly_digest.week_window(week_end) == (start, previous)


@pytest.mark.parametrize("now,expected", [
    (datetime(2025, 9, 6, 12, tzinfo=timezone.utc), date(2025, 9, 5)),   # Saturday
    (datetime(2025, 9, 5, 15, tzinfo=timezone.utc), date(2025, 9, 4)),   # Friday 11:00 ET
    (datetime(2025, 9, 5, 21, tzinfo=timezone.utc), date(2025, 9, 5)),   # Friday 17:00 ET
])
def test_last_closed_session(now, expected):
    assert weekly_digest.last_closed_session(now) == expected


def test_weekly_change_prefers_adjusted_closes_and_needs_both_ends():
    rows = [{"date": "2025-08-29", "close": 100, "adjusted_close": 50},
            {"date": "2025-09-05", "close": 110, "adjusted_close": 60}]
    result = weekly_digest.weekly_change(rows, date(2025, 9, 5), date(2025, 8, 29))
    assert result["change_pct"] == 20.0 and result["basis"] == "adjusted_close" and result["price"] == 110.0
    missing = weekly_digest.weekly_change(rows[1:], date(2025, 9, 5), date(2025, 8, 29))
    assert missing["change_pct"] is None and missing["as_of"] == "2025-09-05"


def digest() -> dict:
    def row(ticker, name, change, group="asset", price=100.0):
        return {"ticker": ticker, "name": name, "group": group, "currency": "USD", "price": price,
                "change_pct": change, "as_of": "2025-09-05", "previous_date": "2025-08-29", "basis": "adjusted_close"}
    return {
        "week_start": "2025-09-02", "week_end": "2025-09-05", "previous_close_date": "2025-08-29",
        "assets": [row("SPY.US", "标普500", 1.2), row("QQQ.US", "纳指100", 2.6), row("IWM.US", "罗素2000", 0.1),
                   row("VIX.INDX", "VIX", -8.0, price=15.2), row("BTC-USD.CC", "比特币", 4.0, price=110000)],
        "sectors": [row(ticker, name, change, "sector") for ticker, name, change in
                    (("XLK.US", "科技", 3.1), ("XLC.US", "通信服务", 2.4), ("XLF.US", "金融", 0.5),
                     ("XLV.US", "医疗保健", -0.2), ("XLE.US", "能源", -1.9), ("XLU.US", "公用事业", -0.8))],
        "rsp_relative_pct": -0.6,
        "rrg": {"as_of": "2025-09-05", "previous_date": "2025-08-29", "items": [
            {"ticker": "XLK.US", "quadrant": "leading", "previous_quadrant": "improving"},
            {"ticker": "XLE.US", "quadrant": "lagging", "previous_quadrant": "lagging"}]},
        "breadth": {"as_of": "2025-09-05", "pct_above_ma50": 62.0, "previous_pct_above_ma50": 55.0,
                    "pct_above_ma200": 58.0, "previous_pct_above_ma200": 57.0, "new_highs": 38, "new_lows": 12,
                    "net_advances": 420},
        "valuation": {"as_of": "2025-08-29", "index_pe": 24.1, "percentile": 85.0, "months": 120},
        "watchlist": [row("NVDA.US", "英伟达", 6.5, "core")],
        "watchlist_signals": {"items": {}},
        "events": {"from": "2025-09-08", "to": "2025-09-12", "economic_available": True, "earnings_available": True,
                   "items": [{"kind": "economic", "name": "CPI", "date": "2025-09-11 08:30:00", "period": "Aug"}]},
        "warnings": [],
    }


def test_weekly_headline_and_card():
    sample = digest()
    headline = weekly_headline(sample)
    assert headline == ("本周美股小幅上涨（标普 +1.20%），纳指 +2.60% 强于罗素 +0.10%；"
                        "科技、通信服务领涨，能源、公用事业领跌（成长板块居前）；"
                        "站上 MA50 的成分股升至 62%（上周 55%）；科技进入轮动领先象限。")
    card = build_weekly_card(sample, headline=headline, site_url="https://example.com")
    assert card["header"]["title"]["content"] == "Quantify 美股周报 · 9月2日–9月5日"
    assert card["header"]["template"] == "green"
    encoded = json.dumps(card, ensure_ascii=False)
    assert "<text_tag color='green'>领先</text_tag> 科技（新进）" in encoded
    assert "站上 MA50 55%" not in encoded and "站上 MA50 62%（<font color='green'>+7pt</font>）" in encoded
    assert "标普500 P/E 24.1，120 个月历史 85% 分位" in encoded
    assert "09-05 52周新高 38 / 新低 12" in encoded
    assert "- 09-11 周四：CPI" in encoded
    table = next(element for element in card["body"]["elements"] if element["tag"] == "table")
    assert table["columns"][2]["display_name"] == "周涨跌"
    assert "/rrg" in encoded and "/market/index-valuation" in encoded
    assert "板块轮动暂不可用" not in encoded


def test_weekly_markdown_spells_out_bases_and_gaps():
    sample = digest()
    sample["rrg"] = None
    content = weekly_digest.render_weekly_markdown(sample)
    assert "周涨跌相对 2025-08-29 收盘" in content
    assert "- 标普500（SPY.US）：100.00 / +1.20%（2025-08-29 → 2025-09-05，复权）" in content
    assert "- 板块轮动暂不可用。" in content
    assert "历史重建估算" in content
    card = build_weekly_card(sample, headline="H。")
    assert "板块轮动暂不可用" in card["body"]["elements"][-1]["content"]


@pytest.mark.asyncio
async def test_weekly_digest_is_audited_and_not_sent_twice(db_session, monkeypatch):
    sent = []

    async def evidence(now):
        return deepcopy(digest())

    async def broadcast(*, title, content, channels=None, card=None):
        sent.append((title, content, card))
        return True

    monkeypatch.setattr(weekly_digest, "collect_weekly_evidence", evidence)
    monkeypatch.setattr(weekly_digest.NotificationManager, "broadcast", broadcast)
    now = datetime(2025, 9, 6, 12, tzinfo=timezone.utc)
    result = await weekly_digest.generate_weekly_digest(now)
    assert result["status"] == "delivered" and result["week_end"] == "2025-09-05"
    run = (await db_session.execute(select(DailyReportRun))).scalar_one()
    assert run.report_type == "weekly_digest" and run.renderer_version == weekly_digest.WEEKLY_RENDERER_VERSION
    assert run.status == "delivered" and run.content == sent[0][1]
    assert sent[0][2]["schema"] == "2.0"
    assert await weekly_digest.generate_weekly_digest(now) == {
        "status": "skipped", "reason": "already-delivered", "week_end": "2025-09-05"}
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_weekly_digest_without_prices_is_not_sent(db_session, monkeypatch):
    async def evidence(now):
        empty = digest()
        for row in empty["assets"] + empty["sectors"]:
            row["change_pct"] = None
        return empty

    async def broadcast(**kwargs):
        pytest.fail("A digest without price evidence must not be sent")

    monkeypatch.setattr(weekly_digest, "collect_weekly_evidence", evidence)
    monkeypatch.setattr(weekly_digest.NotificationManager, "broadcast", broadcast)
    with pytest.raises(RuntimeError):
        await weekly_digest.generate_weekly_digest(datetime(2025, 9, 6, 12, tzinfo=timezone.utc))
    run = (await db_session.execute(select(DailyReportRun))).scalar_one()
    assert run.status == "evidence_failed" and run.notification_delivered is False


def test_scheduler_registers_weekly_digest_only_when_enabled(monkeypatch):
    from core import scheduler
    calls = {}

    class FakeScheduler:
        running = True

        def add_job(self, job, trigger, **kwargs):
            calls[kwargs["id"]] = kwargs

    monkeypatch.setattr(scheduler, "scheduler", FakeScheduler())
    monkeypatch.setattr(scheduler.settings, "WEEKLY_DIGEST_ENABLED", True)
    scheduler.start_scheduler()
    job = calls["weekly_digest"]
    assert (job["day_of_week"], job["hour"], job["minute"]) == ("sat", 8, 0)
    calls.clear()
    monkeypatch.setattr(scheduler.settings, "WEEKLY_DIGEST_ENABLED", False)
    scheduler.start_scheduler()
    assert "weekly_digest" not in calls


@pytest.mark.asyncio
async def test_weekly_breadth_compares_against_the_previous_week_close(db_session):
    from models import DataPublication, MarketBreadthSnapshot, PipelineRun
    run = PipelineRun(pipeline_name="market_breadth", target_date=date(2025, 9, 5), status="published", stage="published")
    db_session.add(run)
    await db_session.flush()
    db_session.add(DataPublication(dataset="market_breadth", as_of_date=date(2025, 9, 5), pipeline_run_id=run.id))
    for day, above50, advances in ((date(2025, 8, 28), 50, 300), (date(2025, 8, 29), 55, 260), (date(2025, 9, 2), 57, 280),
                                   (date(2025, 9, 3), 58, 250), (date(2025, 9, 4), 60, 300), (date(2025, 9, 5), 62, 320)):
        db_session.add(MarketBreadthSnapshot(
            pipeline_run_id=run.id, universe="SP500", date=day, member_count=100, price_count=100, return_count=100,
            advances=advances, declines=500 - advances, unchanged=0, ma20_eligible=100, above_ma20=50,
            ma50_eligible=100, above_ma50=above50, ma200_eligible=100, above_ma200=58,
            high_low_eligible=100, new_high_count=38, new_low_count=12))
    await db_session.commit()
    breadth = await weekly_digest._breadth(date(2025, 9, 5), date(2025, 8, 29))
    assert breadth["as_of"] == "2025-09-05" and breadth["previous_date"] == "2025-08-29"
    assert (breadth["pct_above_ma50"], breadth["previous_pct_above_ma50"]) == (62.0, 55.0)
    assert breadth["net_advances"] == (280 + 250 + 300 + 320) * 2 - 4 * 500  # Sep 2-5 only.
    assert await weekly_digest._breadth(date(2025, 9, 26), date(2025, 9, 19)) is None  # Too old.


@pytest.mark.asyncio
async def test_weekly_evidence_collection_runs_end_to_end_without_published_datasets(db_session, monkeypatch):
    from contextlib import asynccontextmanager
    from datetime import timedelta
    from services import eodhd_client

    @asynccontextmanager
    async def client():
        yield None

    async def bars(ticker, from_date=None, to_date=None, client=None):
        day, end, rows, price = date.fromisoformat(from_date), date.fromisoformat(to_date), [], 100.0
        while day <= end:
            if rsi_monitor.is_us_market_session(day):
                price *= 1.001
                rows.append({"date": day.isoformat(), "close": round(price, 4), "adjusted_close": round(price, 4)})
            day += timedelta(days=1)
        return rows

    async def calendar(kind, start, end, client=None):
        return []

    monkeypatch.setattr(eodhd_client, "create_http_client", client)
    monkeypatch.setattr(eodhd_client, "get_eod_historical_data", bars)
    monkeypatch.setattr(eodhd_client, "get_report_calendar", calendar)
    digest = await weekly_digest.collect_weekly_evidence(datetime(2025, 9, 6, 12, tzinfo=timezone.utc))
    assert (digest["week_start"], digest["week_end"], digest["previous_close_date"]) == ("2025-09-02", "2025-09-05", "2025-08-29")
    assert len(digest["sectors"]) == 11 and all(row["change_pct"] is not None for row in digest["sectors"])
    spy = next(row for row in digest["assets"] if row["ticker"] == "SPY.US")
    assert spy["as_of"] == "2025-09-05" and spy["previous_date"] == "2025-08-29"
    assert spy["change_pct"] == pytest.approx((1.001 ** 4 - 1) * 100, abs=1e-3)  # Labor Day: four sessions.
    assert {row["ticker"] for row in digest["watchlist"]} >= {"AAPL.US", "0700.HK"}
    assert "AAPL.US" in digest["watchlist_signals"]["items"]
    assert digest["events"]["from"] == "2025-09-08" and digest["events"]["to"] == "2025-09-12"
    assert digest["rrg"] is None and digest["breadth"] is None and digest["valuation"] is None
    assert digest["warnings"] == []  # Unpublished datasets are reported once, by the card.
    content = weekly_digest.render_weekly_markdown(digest)
    card = weekly_digest.build_weekly_report_card(digest)
    assert "本周美股" in content and card["schema"] == "2.0"


def test_calendar_keeps_key_releases_and_counts_the_rest():
    from services.notifications.report_card import _calendar_block
    names = ["Fed Hammack Speech", "Inflation Rate", "CPI n.s.a", "Core Inflation Rate", "Cleveland CPI",
             "Philly Fed Employment", "Retail Sales", "Retail Sales Ex Autos", "Initial Jobless Claims",
             "Jobless Claims 4-Week Average"]
    days = ["2026-10-12", "2026-10-14", "2026-10-14", "2026-10-14", "2026-10-14",
            "2026-10-15", "2026-10-15", "2026-10-15", "2026-10-15", "2026-10-15"]
    items = [{"kind": "economic", "name": name, "date": f"{day} 12:30:00"} for name, day in zip(names, days)]
    items += [{"kind": "economic", "name": "Inflation Rate", "date": "2026-10-14 12:30:00", "comparison": "yoy"},
              {"kind": "earnings", "name": "ASML.AS", "date": "2026-10-14", "session": "AfterMarket"},
              {"kind": "earnings", "name": "2330.TW", "date": "2026-10-15", "session": "BeforeMarket"}]
    block = _calendar_block("下周关注", {"items": items}, {"2330.TW": "台积电"}, limit=5)
    assert block["content"] == (
        "**下周关注**\n"
        "- 10-14 周三：CPI · 核心CPI · ASML.AS 财报（盘后）\n"
        "- 10-15 周四：零售销售 · 初请失业金 · 台积电 财报（盘前）\n"
        "<font color='grey'>另有 6 项次要数据或官员讲话；日期按供应商原文</font>")
    assert _calendar_block("今明关注", {"items": items[:1]}, {}, limit=2) is None


def test_every_card_carries_the_feishu_bot_keyword():
    """The group bot rejects messages without its keyword (code 19024)."""
    for card in (build_daily_card(context(), [], report_type="morning_briefing", headline="H。"),
                 build_weekly_card(digest(), headline="H。")):
        assert card["header"]["title"]["content"].startswith("Quantify ")
        assert card["config"]["summary"]["content"].startswith("Quantify ")
