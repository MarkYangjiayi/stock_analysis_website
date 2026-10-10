import json

import httpx
import pytest

from services import ai_assistant, daily_reporter
from services.notifications.feishu_bot import FeishuNotifier
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
    row = {"ticker": "TEST.US", "news": [{"title": "TEST <at id=all></at> **Fake** [link](x)", "link": "https://example.com/a(b)"}]}
    text = daily_reporter._grounded_analysis(row)
    assert "<at " not in text and "**Fake**" not in text
    assert "a%28b%29" in text


def test_repeated_stock_causality_notice_and_duplicate_headlines_are_deduplicated():
    news = [{"title": "AAA BBB Same report", "link": "https://example.com/news"}] * 2
    rows = [{"ticker": f"{ticker}.US", "price_change": -5, "news": news} for ticker in ("AAA", "BBB")]
    report = daily_reporter.render_daily_report(rows, report_type="morning_briefing", market_context=CONTEXT)
    assert report.count("尚未验证为此次涨跌原因") == 1
    assert report.count("Same report") == 2  # Once per stock, not twice per stock.
    assert report.count("https://example.com/news") == 2


@pytest.mark.parametrize("ticker,name,title,expected", [
    ("FDS.US", "FactSet Research Systems Inc", "Enova Withdraws Regulatory Applications", False),
    ("FDS.US", "FactSet Research Systems Inc", "FactSet reports earnings", True),
    ("VAL.US", "Valaris Ltd", "/C O R R E C T I O N -- Crock-Pot/", False),
    ("VAL.US", "Valaris Ltd", "Offshore stocks rally: Valaris rises 6%", True),
    ("PYPL.US", "PayPal Holdings Inc", "Stripe and Advent abandon PayPal pursuit", True),
    ("ON.US", "ON Semiconductor", "ON THE MARKET TODAY", False),
    ("ON.US", "ON Semiconductor", "Earnings for (ON)", True),
    ("ABC.US", None, "ABC earnings", True),
    ("ABC.US", None, "General market outlook", False),
    ("UAL.US", "United Holdings Inc", "United States markets rally", False),
])
def test_saved_company_identity_gates_headlines(ticker, name, title, expected):
    assert daily_reporter._headline_mentions_company(title, {"ticker": ticker, "company_name": name}) is expected


def test_irrelevant_cited_headline_does_not_crowd_out_related_saved_headline():
    row = {"ticker": "VAL.US", "company_name": "Valaris Ltd", "ai_analysis": "原因[1]",
           "news": [{"title": "Crock-Pot correction", "link": "https://example.com/1"},
                    {"title": "Valaris rises", "link": "https://example.com/2"}]}
    rendered = daily_reporter._grounded_analysis(row)
    assert "Crock-Pot" not in rendered
    assert "Valaris rises [2](https://example.com/2)" in rendered
    assert len(row["news"]) == 2  # Raw evidence is not mutated.
    row["news"].pop()
    assert "不展示关联不明条目" in daily_reporter._grounded_analysis(row)


@pytest.mark.asyncio
async def test_notifier_sends_prebuilt_cards_and_keeps_generic_layout(monkeypatch):
    payloads = []
    async def post(client, url, **kwargs):
        payloads.append(kwargs["json"])
        return httpx.Response(200, json={"code": 0}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    notifier = FeishuNotifier()
    notifier.webhook_url = "https://example.com/test-webhook"
    assert await notifier.send("System Status", "Started")
    assert "schema" not in payloads[0]["card"]
    card = {"schema": "2.0", "body": {"elements": []}}
    assert await notifier.send("Quantify", "audit text", card=card)
    assert payloads[1] == {"msg_type": "interactive", "card": card}


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
