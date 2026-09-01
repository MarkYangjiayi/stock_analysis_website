from __future__ import annotations

import pytest
from sqlalchemy import select

from models import DailyReportRun
from services import daily_reporter


def _pypl_anomaly() -> dict:
    return {
        "ticker": "PYPL.US",
        "company_name": "PayPal Holdings Inc",
        "date": "2026-08-28",
        "quote_timestamp": "2026-08-28T13:18:00Z",
        "price_change": -14.25,
        "ai_analysis": (
            "核心原因：Stripe与Advent放弃对PayPal的500亿美元收购谈判，"
            "交易告吹[2]。该消息直接引发股价重挫[3]。"
        ),
        "attribution_status": "completed",
        "news": [
            {
                "title": "Market movers",
                "link": "https://example.com/market",
            },
            {
                "title": "Stripe and Advent abandon PayPal pursuit",
                "link": "https://example.com/deal-collapse",
            },
            {
                "title": "PayPal drops after bid collapse",
                "link": "https://example.com/paypal-drops",
            },
        ],
        "top_news_links": [
            "https://example.com/market",
            "https://example.com/deal-collapse",
            "https://example.com/paypal-drops",
        ],
    }


def test_morning_report_preserves_pypl_grounded_attribution():
    content = daily_reporter.render_daily_report(
        [_pypl_anomaly()],
        report_type="morning_briefing",
    )

    assert "美股开盘速递｜2026-08-28" in content
    assert "PYPL -14.25%" in content
    assert "Stripe与Advent" in content
    assert "收购谈判" in content
    assert "[2](https://example.com/deal-collapse)" in content
    assert "[3](https://example.com/paypal-drops)" in content
    assert "利润率承压" not in content
    assert "下调全年指引" not in content
    assert "操作建议" not in content


def test_no_news_report_fails_closed_instead_of_reusing_analysis():
    anomaly = {
        "ticker": "NONE.US",
        "date": "2026-08-28",
        "price_change": 8.0,
        "attribution_status": "no_news",
        "ai_analysis": "买入并追涨，这是一条不应显示的旧归因。",
        "news": [],
    }

    content = daily_reporter.render_daily_report(
        [anomaly],
        report_type="morning_briefing",
    )

    assert "缺乏明确新闻催化剂" in content
    assert "买入并追涨" not in content


def test_uncited_analysis_gets_source_links_and_rejects_unsafe_urls():
    anomaly = _pypl_anomaly()
    anomaly["ai_analysis"] = "收购溢价消失导致股价下跌。"
    anomaly["news"] = [
        {"title": "Unsafe", "link": "javascript:alert(1)"},
        {"title": "Evidence", "link": "https://example.com/evidence"},
    ]

    content = daily_reporter.render_daily_report(
        [anomaly],
        report_type="morning_briefing",
    )

    assert "javascript:" not in content
    assert "来源：[2](https://example.com/evidence)" in content


def test_unresolved_citation_fails_closed():
    anomaly = _pypl_anomaly()
    anomaly["ai_analysis"] = (
        "Stripe与Advent放弃收购[2]，另有无法验证的事件[4]。"
    )

    content = daily_reporter.render_daily_report(
        [anomaly],
        report_type="morning_briefing",
    )

    assert "归因引用无法与已保存新闻匹配" in content
    assert "无法验证的事件" not in content
    assert "Stripe与Advent放弃收购" not in content


@pytest.mark.asyncio
async def test_morning_report_is_delivered_and_audited(db_session, monkeypatch):
    broadcasts: list[dict] = []

    async def fake_scan(*, trigger, limit_count):
        assert trigger == "morning_briefing"
        assert limit_count == 5
        return [_pypl_anomaly()]

    async def fake_broadcast(*, title, content, channels=None):
        broadcasts.append({"title": title, "content": content})
        return True

    monkeypatch.setattr(
        daily_reporter,
        "run_persisted_anomaly_scan",
        fake_scan,
    )
    monkeypatch.setattr(
        daily_reporter.NotificationManager,
        "broadcast",
        fake_broadcast,
    )

    result = await daily_reporter.generate_morning_briefing()
    run = (
        await db_session.execute(select(DailyReportRun))
    ).scalar_one()

    assert result == {
        "status": "delivered",
        "report_type": "morning_briefing",
        "report_run_id": run.id,
        "anomalies": 1,
    }
    assert broadcasts[0]["title"] == "🌅 Quantify 美股开盘速递"
    assert broadcasts[0]["content"] == run.content
    assert "Stripe与Advent" in run.content
    assert run.renderer_version == daily_reporter.REPORT_RENDERER_VERSION
    assert run.status == "delivered"
    assert run.notification_delivered is True
    assert run.source_results[0]["ticker"] == "PYPL.US"
    assert run.finished_at is not None
    assert run.error_message is None


@pytest.mark.asyncio
async def test_evidence_failure_is_audited_and_raised(db_session, monkeypatch):
    async def failed_scan(*, trigger, limit_count):
        raise RuntimeError("Current quotes are unavailable")

    async def unexpected_broadcast(*, title, content, channels=None):
        pytest.fail("A report without evidence must not be broadcast")

    monkeypatch.setattr(
        daily_reporter,
        "run_persisted_anomaly_scan",
        failed_scan,
    )
    monkeypatch.setattr(
        daily_reporter.NotificationManager,
        "broadcast",
        unexpected_broadcast,
    )

    with pytest.raises(RuntimeError, match="Current quotes are unavailable"):
        await daily_reporter.generate_morning_briefing()

    run = (
        await db_session.execute(select(DailyReportRun))
    ).scalar_one()
    assert run.status == "evidence_failed"
    assert run.source_results == []
    assert run.content == ""
    assert run.notification_delivered is False
    assert run.error_message == "Current quotes are unavailable"
    assert run.finished_at is not None


@pytest.mark.asyncio
async def test_delivery_rejection_is_audited_and_raised(db_session, monkeypatch):
    async def fake_scan(*, trigger, limit_count):
        return [_pypl_anomaly()]

    async def rejected_broadcast(*, title, content, channels=None):
        return False

    monkeypatch.setattr(
        daily_reporter,
        "run_persisted_anomaly_scan",
        fake_scan,
    )
    monkeypatch.setattr(
        daily_reporter.NotificationManager,
        "broadcast",
        rejected_broadcast,
    )

    with pytest.raises(
        RuntimeError,
        match="No notification channel accepted",
    ):
        await daily_reporter.generate_morning_briefing()

    run = (
        await db_session.execute(select(DailyReportRun))
    ).scalar_one()
    assert run.status == "delivery_failed"
    assert run.notification_delivered is False
    assert "No notification channel accepted" in run.error_message
    assert run.finished_at is not None


@pytest.mark.asyncio
async def test_post_market_report_uses_same_grounded_path(db_session, monkeypatch):
    requested: list[tuple[str, int]] = []

    async def fake_scan(*, trigger, limit_count):
        requested.append((trigger, limit_count))
        return [_pypl_anomaly()]

    async def fake_broadcast(*, title, content, channels=None):
        assert title == "🌃 Quantify 美股盘后总结"
        assert "美股盘后总结｜2026-08-28" in content
        assert "Stripe与Advent" in content
        return True

    monkeypatch.setattr(
        daily_reporter,
        "run_persisted_anomaly_scan",
        fake_scan,
    )
    monkeypatch.setattr(
        daily_reporter.NotificationManager,
        "broadcast",
        fake_broadcast,
    )

    result = await daily_reporter.generate_post_market_summary()

    assert requested == [("post_market_summary", 10)]
    assert result["status"] == "delivered"
    assert result["report_type"] == "post_market_summary"
