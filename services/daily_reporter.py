from __future__ import annotations

from collections import Counter
import logging
import re
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import update

from core.time_utils import utc_now
from database import async_session_maker
from models import DailyReportRun
from services.anomaly_scans import run_persisted_anomaly_scan
from services.notifications import NotificationManager


logger = logging.getLogger(__name__)
REPORT_RENDERER_VERSION = "evidence-v1"
_CITATION_PATTERN = re.compile(r"\[(\d+)]")
_NO_CATALYST = "缺乏明确新闻催化剂，可能为资金面或技术面行为。"


def _safe_link(value: Any) -> str:
    normalized = str(value or "").strip()
    parsed = urlparse(normalized)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return normalized
    return ""


def _compact_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _display_ticker(value: Any) -> str:
    ticker = _compact_text(value).upper()
    return ticker[:-3] if ticker.endswith(".US") else ticker


def _format_move(value: Any) -> str:
    try:
        move = float(value)
    except (TypeError, ValueError):
        return "涨跌幅未知"
    return f"{move:+.2f}%"


def _report_date(anomalies: list[dict[str, Any]]) -> str:
    dates = [
        _compact_text(anomaly.get("date"))
        for anomaly in anomalies
        if _compact_text(anomaly.get("date"))
    ]
    if not dates:
        return "盘中实时更新"
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
    if not analysis:
        return "归因信息不可用，本次仅展示行情异动。"

    cited_links: set[int] = set()

    def link_citation(match: re.Match[str]) -> str:
        source_number = int(match.group(1))
        link = links.get(source_number)
        if not link:
            return match.group(0)
        cited_links.add(source_number)
        return f"[{source_number}]({link})"

    grounded = _CITATION_PATTERN.sub(link_citation, analysis)
    if links and not cited_links:
        source_markers = " ".join(
            f"[{index}]({link})"
            for index, link in links.items()
        )
        grounded = f"{grounded} 来源：{source_markers}"
    return grounded


def render_daily_report(
    anomalies: list[dict[str, Any]],
    *,
    report_type: str,
) -> str:
    """Render an evidence-bound report without asking an LLM to infer causes again."""
    if report_type not in {"morning_briefing", "post_market_summary"}:
        raise ValueError(f"Unsupported daily report type: {report_type}")

    report_name = (
        "美股开盘速递"
        if report_type == "morning_briefing"
        else "美股盘后总结"
    )
    if not anomalies:
        quiet_message = (
            "早盘扫描完成：当前市场暂无显著异动标的。"
            if report_type == "morning_briefing"
            else "盘后扫描完成：今日市场平稳收盘，暂无特大级别异动。"
        )
        return f"**{report_name}｜{_report_date(anomalies)}**\n\n{quiet_message}"

    lines = [
        f"**{report_name}｜{_report_date(anomalies)}**",
        "",
        "**核心异动与已验证驱动**",
        "",
    ]
    for anomaly in anomalies:
        ticker = _display_ticker(anomaly.get("ticker")) or "UNKNOWN"
        move = _format_move(anomaly.get("price_change"))
        lines.append(
            f"- **{ticker} {move}**：{_grounded_analysis(anomaly)}"
        )
    lines.extend([
        "",
        "> 说明：驱动仅依据扫描时抓取并保存的新闻；没有可靠证据时不推断原因，也不自动生成交易建议。",
    ])
    return "\n".join(lines)


async def _create_report_run(
    *,
    report_type: str,
    anomalies: list[dict[str, Any]],
    content: str,
) -> int:
    async with async_session_maker() as db, db.begin():
        run = DailyReportRun(
            report_type=report_type,
            renderer_version=REPORT_RENDERER_VERSION,
            status="rendered",
            source_results=anomalies,
            content=content,
            notification_delivered=False,
        )
        db.add(run)
        await db.flush()
        return run.id


async def _finish_report_run(
    report_run_id: int,
    *,
    delivered: bool,
    error_message: str | None = None,
) -> None:
    async with async_session_maker() as db, db.begin():
        await db.execute(
            update(DailyReportRun)
            .where(DailyReportRun.id == report_run_id)
            .values(
                status="delivered" if delivered else "delivery_failed",
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
    anomalies = await run_persisted_anomaly_scan(
        trigger=report_type,
        limit_count=limit_count,
    )
    content = render_daily_report(anomalies, report_type=report_type)
    report_run_id = await _create_report_run(
        report_type=report_type,
        anomalies=anomalies,
        content=content,
    )

    try:
        delivered = await NotificationManager.broadcast(
            title=notification_title,
            content=content,
        )
        if not delivered:
            raise RuntimeError(
                "No notification channel accepted the daily report"
            )
    except Exception as exc:
        await _finish_report_run(
            report_run_id,
            delivered=False,
            error_message=str(exc),
        )
        logger.exception(
            "Failed to deliver %s report run %s",
            report_type,
            report_run_id,
        )
        raise

    await _finish_report_run(report_run_id, delivered=True)
    return {
        "status": "delivered",
        "report_type": report_type,
        "report_run_id": report_run_id,
        "anomalies": len(anomalies),
    }


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
