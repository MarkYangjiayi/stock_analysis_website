import json

import httpx
import pytest

from services import ai_assistant, daily_reporter
from services.notifications.feishu_bot import FeishuNotifier
from services.notifications.report_card import build_daily_report_card
from services.report_renderer import value


CONTEXT = {"report_date": "2026-09-14", "captured_at": "2026-09-14T17:00:00+00:00"}


@pytest.mark.parametrize("timestamp, expected, fresh", [
    (None, "报价时间缺失", False),
    ("nonsense", "报价时间缺失", False),
    ("2026-09-14T16:45:00", "无明确时区", False),
    ("2026-09-14T17:20:00Z", "报价时间异常", False),
    ("2026-09-11T20:00:00Z", "历史快照", False),
    ("2026-09-14T13:19:00Z", "盘前报价", False),
    ("2026-09-14T14:30:00Z", "旧快照（距采集 150 分钟）", False),
    ("2026-09-14T16:45:00Z", "12:45 EDT · 延迟报价", True),
    ("2026-09-15T00:45:00+08:00", "12:45 EDT · 延迟报价", True),
])
def test_each_stock_quote_has_explicit_time_and_validated_freshness(timestamp, expected, fresh):
    label, actual = daily_reporter.anomaly_observation({"ticker": "TEST.US", "quote_timestamp": timestamp}, CONTEXT)
    assert expected in label
    assert actual is fresh


def test_after_close_does_not_mark_completed_quote_old_just_because_of_age():
    context = {**CONTEXT, "captured_at": "2026-09-14T22:00:00Z"}
    row = {"ticker": "TEST.US", "quote_timestamp": "2026-09-14T20:00:00Z"}
    assert daily_reporter.anomaly_observation(row, context)[1]
    row["quote_timestamp"] = "2026-09-14T19:30:00Z"
    assert "收盘行情待更新" in daily_reporter.anomaly_observation(row, context)[0]


def test_quote_time_uses_dst_and_does_not_guess_missing_capture_time():
    row = {"quote_timestamp": "2026-01-05T15:15:00Z"}
    label, fresh = daily_reporter.anomaly_observation(row, None)
    assert "10:15 EST" in label and "未核验" in label
    assert not fresh


@pytest.mark.parametrize("raw", [None, True, "nan", float("inf"), float("-inf")])
def test_invalid_anomaly_moves_are_unknown(raw):
    assert daily_reporter._format_move(raw) == "涨跌幅未知"


@pytest.mark.parametrize("raw,expected", [
    (-0.003923, "微降（不足 0.01%）"), (0.003, "微升（不足 0.01%）"),
    (-0.0, "0.00%"), (0, "0.00%"), (-0.015, "-0.01%"), (1.25, "+1.25%"),
])
def test_tiny_returns_keep_direction_without_signed_zero(raw, expected):
    assert value(raw, "%", True) == expected
    assert daily_reporter._format_move(raw) == expected


def test_no_sources_means_no_causal_claim_even_without_numbered_citations():
    row = {"ai_analysis": "确定由资金流出引发，大胆买入", "attribution_status": "completed", "news": []}
    assert "无法确认" in daily_reporter._grounded_analysis(row)
    assert "大胆买入" not in daily_reporter._grounded_analysis(row)


def test_report_places_quality_upfront_and_breaks_stock_news_from_quote_line():
    row = {"ticker": "TEST.US", "price_change": -5, "quote_timestamp": "2026-09-14T13:19:00Z", "attribution_status": "no_news"}
    report = daily_reporter.render_daily_report([row], report_type="morning_briefing", market_context=CONTEXT)
    assert report.index("数据提示") < report.index("核心变化")
    assert "个股异动 1/1" in report
    assert "盘前报价（非开盘后行情）<br>" in report
    assert "\n> 说明" not in report


def test_headline_markdown_and_mention_are_not_executable():
    row = {"news": [{"title": "<at id=all></at> **Fake** [link](x)", "link": "https://example.com/a(b)"}]}
    text = daily_reporter._grounded_analysis(row)
    assert "<at " not in text and "**Fake**" not in text
    assert "a%28b%29" in text


def sample_report():
    return """**美股盘后总结｜2026-09-14**
采集于 09-14 16:30 EDT；延迟行情，非实时或正式收盘价。

**数据提示**
- 行情当期可用 1/2；历史或待更新 0；缺失 1。
- 模块缺口：市场宽度。

**核心变化**
- SPY +1.00%

**全球股票市场**
- SPY 100.00 / +1.00%；09-14 20:00 UTC

**核心资产与自选股**
- AAPL 200.00 / +2.00%；09-14 20:00 UTC

**个股异动与新闻线索**
- TEST +5.00% · 报价 09-14 16:00 EDT
  新闻线索：[1](https://example.com/news)。尚未验证为此次涨跌原因。

**与开盘报告对比**
- SPY 微降（不足 0.01%）

**今明交易日关注**
- 日历暂不可用，不能据此判断没有重要事件。

**数据口径与覆盖**
- 来源：[行情](https://example.com/quotes)
说明：不生成交易建议。
"""


def test_card_is_bounded_grouped_and_keeps_all_details_without_callbacks():
    card = build_daily_report_card("Quantify 测试", sample_report())
    assert card["schema"] == "2.0"
    assert card["config"]["width_mode"] == "default"
    elements = card["body"]["elements"]
    assert len(elements) == 5
    assert elements[0]["tag"] == "column_set"
    assert "市场宽度" in elements[1]["content"]
    panels = elements[2:]
    assert all(p["tag"] == "collapsible_panel" and not p["expanded"] for p in panels)
    encoded = json.dumps(card, ensure_ascii=False)
    for expected in ("SPY 100.00", "AAPL 200.00", "报价 09-14 16:00 EDT", "https://example.com/news", "微降", "日历暂不可用", "https://example.com/quotes"):
        assert expected in encoded
    assert "callback" not in encoded and "behaviors" not in encoded


@pytest.mark.asyncio
async def test_notifier_only_uses_new_layout_for_daily_reports(monkeypatch):
    payloads = []
    async def post(client, url, **kwargs):
        payloads.append(kwargs["json"])
        return httpx.Response(200, json={"code": 0}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    notifier = FeishuNotifier()
    notifier.webhook_url = "https://example.com/test-webhook"
    assert await notifier.send("System Status", "Started")
    assert "schema" not in payloads[0]["card"]
    assert await notifier.send("Quantify", sample_report(), card_layout="daily_report")
    assert payloads[1]["card"]["schema"] == "2.0"


@pytest.mark.asyncio
async def test_prompt_separates_facts_relation_and_limits(monkeypatch):
    prompts = []
    async def generate(prompt):
        prompts.append(prompt)
        return "新闻事实：公布指引[1]。关联判断：可能相关。证据边界：无法确认原因。"
    monkeypatch.setattr(ai_assistant.settings, "DEEPSEEK_API_KEY", "test-only")
    monkeypatch.setattr(ai_assistant, "generate_deepseek_text", generate)
    await ai_assistant.generate_anomaly_attribution("TEST.US", -5, ["Guidance update"])
    assert "新闻事实" in prompts[0] and "证据边界" in prompts[0]
    assert "不能把时间先后" in prompts[0]
    assert "过去 24 小时" not in prompts[0]
    assert "请严格根据这些新闻，分析导致" not in prompts[0]
