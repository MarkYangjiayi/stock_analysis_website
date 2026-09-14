from __future__ import annotations

from collections import Counter
import asyncio
from datetime import datetime, timedelta, timezone
import logging
import re
from typing import Any
from urllib.parse import quote, urlparse

from sqlalchemy import select, update

from core.time_utils import utc_now
from database import async_session_maker
from models import DailyReportRun
from services.anomaly_scans import run_persisted_anomaly_scan
from services.notifications import NotificationManager
from services.report_market import NY, Instrument, collect_market_context, session_context
from services.report_renderer import compact, instant, number, quality_summary, render_events_and_quality, render_market_sections, value


logger = logging.getLogger(__name__)
REPORT_RENDERER_VERSION = "cross-asset-v2.1"
_CITATION_PATTERN = re.compile(r"\[(\d+)]")
_NO_CATALYST = "本次未检索到可用新闻，无法确认异动原因。"
_INVALID_CITATIONS = "归因引用无法与已保存新闻匹配，本次仅展示行情异动。"


def _safe_link(value: Any) -> str:
    normalized = str(value or "").strip()
    parsed = urlparse(normalized)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        if parsed.username or parsed.password or any(char.isspace() for char in normalized):
            return ""
        return quote(normalized, safe=":/?#[]@!$&'*+,;=%~_-")
    return ""


def _compact_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _display_ticker(value: Any) -> str:
    ticker = _compact_text(value).upper()
    return ticker[:-3] if ticker.endswith(".US") else ticker


def _format_move(raw: Any) -> str:
    move = number(raw)
    if move is None:
        return "涨跌幅未知"
    return value(move, "%", signed=True)


def _report_date(anomalies: list[dict[str, Any]]) -> str:
    dates = [
        _compact_text(anomaly.get("date"))
        for anomaly in anomalies
        if _compact_text(anomaly.get("date"))
    ]
    if not dates:
        return datetime.now(NY).date().isoformat()
    return Counter(dates).most_common(1)[0][0]


def _source_links(news: Any) -> dict[int, str]:
    if not isinstance(news, list):
        return {}
    links: dict[int, str] = {}
    for index, item in enumerate(news, start=1):
        if not isinstance(item, dict):
            continue
        link = _safe_link(item.get("link"))
        if link:
            links[index] = link
    return links


def _grounded_analysis(anomaly: dict[str, Any]) -> str:
    status = _compact_text(anomaly.get("attribution_status"))
    analysis = _compact_text(anomaly.get("ai_analysis"))
    links = _source_links(anomaly.get("news"))

    if status == "no_news":
        return _NO_CATALYST
    citation_numbers = {
        int(match.group(1))
        for match in _CITATION_PATTERN.finditer(analysis)
    }
    if citation_numbers and not citation_numbers.issubset(links):
        return _INVALID_CITATIONS

    if not links:
        return "可核验新闻来源不足，无法确认异动原因。"
    # A valid citation only proves the article exists, not that the model's
    # causal claim follows from it. Render the saved headline evidence instead
    # of laundering old/free-form model conclusions as verified facts.
    indices = sorted(citation_numbers) if citation_numbers else list(links)
    headlines = []
    news = anomaly.get("news", [])
    for index in indices[:3]:
        title = compact(news[index - 1].get("title")) or "标题未提供"
        title = title[:120] + ("…" if len(title) > 120 else "")
        title = re.sub(r"([\\`*_\[\]()!~])", r"\\\1", title)
        headlines.append(f"{title} [{index}]({links[index]})")
    return "新闻线索（原标题）：" + "；".join(headlines) + "。关联判断：尚未验证为此次涨跌原因。"


def anomaly_observation(anomaly: dict, context: dict | None) -> tuple[str, bool]:
    quoted = instant(anomaly.get("quote_timestamp"))
    if quoted is None:
        return "报价时间缺失或无明确时区 · 新鲜度未核验", False
    label = quoted.astimezone(NY).strftime("报价 %m-%d %H:%M %Z")
    captured = instant((context or {}).get("captured_at"))
    if captured is None:
        return f"{label} · 新鲜度未核验", False
    if quoted > captured + timedelta(minutes=2):
        return f"{label} · 报价时间异常（晚于采集）", False
    if quoted.astimezone(NY).date().isoformat() != context.get("report_date"):
        return f"{label} · 历史快照（非本报告交易日）", False
    session = session_context(Instrument(str(anomaly.get("ticker")), "", "core"), captured)
    opens, closes = instant(session.get("open")), instant(session.get("close"))
    if not opens or not closes or session.get("expected_date") != context.get("report_date"):
        return f"{label} · 交易日未核验", False
    if quoted < opens:
        return f"{label} · 盘前报价（非开盘后行情）", False
    if captured >= closes and quoted < closes - timedelta(minutes=5):
        return f"{label} · 收盘行情待更新", False
    if captured < closes and captured - quoted > timedelta(minutes=45):
        minutes = int((captured - quoted).total_seconds() // 60)
        return f"{label} · 旧快照（距采集 {minutes} 分钟）", False
    status = "收盘附近延迟报价" if captured >= closes else "延迟报价"
    if quoted > closes + timedelta(minutes=5):
        status = "收盘后快照，非正式收盘价"
    return f"{label} · {status}", True


def render_daily_report(
    anomalies: list[dict[str, Any]],
    *,
    report_type: str,
    market_context: dict[str, Any] | None = None,
) -> str:
    """Render an evidence-bound report without asking an LLM to infer causes again."""
    if report_type not in {"morning_briefing", "post_market_summary"}:
        raise ValueError(f"Unsupported daily report type: {report_type}")

    report_name = (
        "美股开盘速递"
        if report_type == "morning_briefing"
        else "美股盘后总结"
    )
    report_date = market_context.get("report_date") if market_context else _report_date(anomalies)
    lines = [
        f"**{report_name}｜{report_date}**",
        "",
    ]
    if market_context:
        captured = instant(market_context.get("captured_at"))
        when = captured.astimezone(NY).strftime("%m-%d %H:%M %Z") if captured else "时间未知"
        lines.extend([f"采集于 {when}；延迟行情，非实时或正式收盘价。", "", "**数据提示**", "",
                      *quality_summary(market_context)])
        stale = sum(not anomaly_observation(row, market_context)[1] for row in anomalies)
        if stale:
            lines.append(f"- 个股异动 {stale}/{len(anomalies)} 项为历史、盘前、待更新或未核验报价，勿作当前行情。")
        lines.extend(["",
                      *render_market_sections(market_context, report_type), ""])
    lines.extend(["**个股异动与新闻线索**", ""])
    if not anomalies:
        lines.append("- 本次扫描没有可展示的个股异动；不据此判断整体市场平稳。")
    for anomaly in anomalies:
        ticker = _display_ticker(anomaly.get("ticker")) or "UNKNOWN"
        move = _format_move(anomaly.get("price_change"))
        lines.append(
            f"- **{ticker} {move}** · {anomaly_observation(anomaly, market_context)[0]}<br>\n"
            f"  {_grounded_analysis(anomaly)}"
        )
    if market_context:
        lines.extend(render_events_and_quality(market_context))
    lines.extend([
        "",
        "说明：新闻标题仅为已保存线索，不证明涨跌因果；不推测资金行为，也不生成交易建议。",
    ])
    return "\n".join(lines)


async def _create_report_run(
    *,
    report_type: str,
) -> int:
    async with async_session_maker() as db, db.begin():
        run = DailyReportRun(
            report_type=report_type,
            renderer_version=REPORT_RENDERER_VERSION,
            status="collecting_evidence",
            source_results=[],
            content="",
            notification_delivered=False,
        )
        db.add(run)
        await db.flush()
        return run.id


async def _record_report_evidence(
    report_run_id: int,
    anomalies: list[dict[str, Any]],
    market_context: dict[str, Any],
) -> None:
    async with async_session_maker() as db, db.begin():
        await db.execute(
            update(DailyReportRun)
            .where(DailyReportRun.id == report_run_id)
            .values(
                status="evidence_collected",
                source_results=anomalies,
                market_context=market_context,
            )
        )


async def _record_rendered_report(
    report_run_id: int,
    content: str,
) -> None:
    async with async_session_maker() as db, db.begin():
        await db.execute(
            update(DailyReportRun)
            .where(DailyReportRun.id == report_run_id)
            .values(status="rendered", content=content)
        )


async def _finish_report_run(
    report_run_id: int,
    *,
    status: str,
    delivered: bool,
    error_message: str | None = None,
) -> None:
    async with async_session_maker() as db, db.begin():
        await db.execute(
            update(DailyReportRun)
            .where(DailyReportRun.id == report_run_id)
            .values(
                status=status,
                notification_delivered=delivered,
                error_message=error_message,
                finished_at=utc_now(),
            )
        )


async def _generate_and_broadcast(
    *,
    report_type: str,
    limit_count: int,
    notification_title: str,
) -> dict[str, Any]:
    report_run_id = await _create_report_run(
        report_type=report_type,
    )

    now = datetime.now(timezone.utc)
    scan_result, context_result = await asyncio.gather(
        run_persisted_anomaly_scan(trigger=report_type, limit_count=limit_count),
        collect_market_context(now=now),
        return_exceptions=True,
    )
    for result in (scan_result, context_result):
        if isinstance(result, asyncio.CancelledError):
            raise result
    if isinstance(context_result, Exception):
        market_context = {
            "report_date": now.astimezone(NY).date().isoformat(),
            "captured_at": now.isoformat(), "quotes": [],
            "warnings": ["跨资产数据采集失败，本次覆盖不完整。"],
        }
    else:
        market_context = context_result
    if isinstance(scan_result, Exception):
        market_context.setdefault("warnings", []).append("个股异动扫描失败，当前报告仅展示其他可用证据。")
        anomalies = []
    else:
        anomalies = scan_result
    has_market_evidence = any(row.get("price") is not None for row in market_context.get("quotes", [])) or market_context.get("treasury") or market_context.get("breadth")
    if isinstance(scan_result, Exception) and not has_market_evidence:
        exc = scan_result
        await _finish_report_run(
            report_run_id,
            status="evidence_failed",
            delivered=False,
            error_message=str(exc),
        )
        raise exc

    if report_type == "post_market_summary":
        try:
            market_context["morning_snapshot"] = await _morning_snapshot(market_context["report_date"])
        except Exception:
            market_context.setdefault("warnings", []).append("当天早报快照读取失败，无法完成前后对比。")
    await _record_report_evidence(report_run_id, anomalies, market_context)
    try:
        content = render_daily_report(anomalies, report_type=report_type, market_context=market_context)
        await _record_rendered_report(report_run_id, content)
    except Exception as exc:
        await _finish_report_run(
            report_run_id,
            status="render_failed",
            delivered=False,
            error_message=str(exc),
        )
        logger.exception(
            "Failed to render %s report run %s",
            report_type,
            report_run_id,
        )
        raise

    try:
        delivered = await NotificationManager.broadcast(
            title=notification_title,
            content=content,
            card_layout="daily_report",
        )
        if not delivered:
            raise RuntimeError(
                "No notification channel accepted the daily report"
            )
    except Exception as exc:
        await _finish_report_run(
            report_run_id,
            status="delivery_failed",
            delivered=False,
            error_message=str(exc),
        )
        logger.exception(
            "Failed to deliver %s report run %s",
            report_type,
            report_run_id,
        )
        raise

    await _finish_report_run(
        report_run_id,
        status="delivered",
        delivered=True,
    )
    return {
        "status": "delivered",
        "report_type": report_type,
        "report_run_id": report_run_id,
        "anomalies": len(anomalies),
    }


async def _morning_snapshot(report_date: str) -> dict | None:
    async with async_session_maker() as db:
        context = await db.scalar(
            select(DailyReportRun.market_context)
            .where(DailyReportRun.report_type == "morning_briefing",
                   DailyReportRun.status == "delivered",
                   DailyReportRun.notification_delivered.is_(True),
                   DailyReportRun.market_context["report_date"].as_string() == report_date)
            .order_by(DailyReportRun.id.desc()).limit(1)
        )
    # Only persist the fields needed by comparisons, avoiding nested reports.
    return {"report_date": context["report_date"], "quotes": context.get("quotes", [])} if context else None


async def generate_morning_briefing() -> dict[str, Any]:
    """Generate an evidence-bound morning briefing and broadcast it."""
    logger.info("Executing Morning Briefing Task...")
    return await _generate_and_broadcast(
        report_type="morning_briefing",
        limit_count=5,
        notification_title="🌅 Quantify 美股开盘速递",
    )


async def generate_post_market_summary() -> dict[str, Any]:
    """Generate an evidence-bound post-market summary and broadcast it."""
    logger.info("Executing Post Market Summary Task...")
    return await _generate_and_broadcast(
        report_type="post_market_summary",
        limit_count=10,
        notification_title="🌃 Quantify 美股盘后总结",
    )
