"""Feishu alert when a published dataset falls behind the latest completed session.

A failed or deferred pipeline leaves the previous publication serving. That is
right for readers but silent for the owner: in October 2026 market breadth and
the index P/E sat at 2026-10-13 for days because their membership dependency
kept failing. This check runs after the overnight pipelines, lists every
lagging dataset with the latest run's failure message, and reports each
dataset once per expected session.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from core.config import settings
from core.time_utils import utc_now
from core.trading_calendar import is_us_market_session, latest_completed_us_session
from database import async_session_maker
from models import DataFreshnessAlert, DataPublication, PipelineRun
from services.notifications.manager import NotificationManager

logger = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
ALERT_TITLE = "⚠️ Quantify 数据过期提醒"
MAX_ERROR_CHARS = 240


@dataclass(frozen=True)
class WatchedDataset:
    dataset: str
    label: str
    # PipelineRun.pipeline_name of the job that publishes the dataset.
    pipeline: str


WATCHED_DATASETS = (
    WatchedDataset("screener", "筛选器快照", "daily_screener"),
    WatchedDataset("price_history", "日线价格", "daily_screener"),
    WatchedDataset("rrg_price_history", "板块 ETF 价格", "rrg_price_history_backfill"),
    WatchedDataset("universe_history", "标普 500 历史成分", "historical_universe_sync"),
    WatchedDataset("market_breadth", "市场宽度", "market_breadth"),
    WatchedDataset("factors", "因子截面", "factor_cross_section"),
    WatchedDataset("index_valuation", "标普 500 历史市盈率", "index_valuation"),
)
THEME_TRENDS = WatchedDataset("theme_trends", "题材热度", "theme_trends")


def watched_datasets() -> tuple[WatchedDataset, ...]:
    return WATCHED_DATASETS + ((THEME_TRENDS,) if settings.THEMES_ENABLED else ())


def sessions_behind(published: Optional[date], expected: date) -> Optional[int]:
    """US market sessions after ``published`` up to and including ``expected``."""
    if published is None:
        return None
    count = 0
    cursor = published + timedelta(days=1)
    while cursor <= expected:
        count += is_us_market_session(cursor)
        cursor += timedelta(days=1)
    return count


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if value is not None else None


async def stale_datasets(expected: date) -> list[dict[str, Any]]:
    """Every watched dataset whose latest publication predates ``expected``."""
    stale: list[dict[str, Any]] = []
    async with async_session_maker() as db:
        for watched in watched_datasets():
            published = await db.scalar(
                select(func.max(DataPublication.as_of_date))
                .join(PipelineRun, PipelineRun.id == DataPublication.pipeline_run_id)
                .where(
                    DataPublication.dataset == watched.dataset,
                    DataPublication.status == "published",
                    PipelineRun.status == "published",
                )
            )
            if published is not None and published >= expected:
                continue
            last_run = await db.scalar(
                select(PipelineRun)
                .where(PipelineRun.pipeline_name == watched.pipeline)
                .order_by(PipelineRun.started_at.desc(), PipelineRun.id.desc())
                .limit(1)
            )
            stale.append({
                "dataset": watched.dataset,
                "label": watched.label,
                "published": published,
                "sessions_behind": sessions_behind(published, expected),
                "last_run": None if last_run is None else {
                    "status": last_run.status,
                    "target_date": _iso(last_run.target_date),
                    "error": (last_run.error_message or "").strip()[:MAX_ERROR_CHARS] or None,
                },
            })
    return stale


def _run_line(item: dict[str, Any], expected: date) -> str:
    run = item["last_run"]
    if run is None:
        return "没有找到运行记录。"
    target = run["target_date"] or "日期未知"
    if run["status"] == "failed":
        return f"最近一次运行（{target}）失败：{run['error'] or '没有错误信息'}"
    if run["status"] == "running":
        return f"最近一次运行（{target}）仍在进行中。"
    if run["target_date"] is not None and run["target_date"] < expected.isoformat():
        # Breadth and index valuation return "deferred" without a run when an
        # upstream publication is missing, so the last run is the old success.
        return f"此后没有新的运行（最近一次是 {target}，{run['status']}），通常是上游数据还没发布。"
    return f"最近一次运行（{target}）状态：{run['status']}。"


def render_alert(expected: date, stale: list[dict[str, Any]]) -> str:
    lines = [f"**应更新到 {expected.isoformat()}（最近完成的美股交易日），以下数据仍停在旧日期：**", ""]
    for item in stale:
        if item["published"] is None:
            status = "从未发布"
        else:
            status = f"最新 {item['published'].isoformat()}，落后 {item['sessions_behind']} 个交易日"
        lines.append(f"- **{item['label']}**（`{item['dataset']}`）：{status}")
        lines.append(f"  {_run_line(item, expected)}")
    lines.extend(["", "网站继续显示最后一次完整发布的数据；同一交易日只提醒一次。"])
    return "\n".join(lines)


async def run_data_freshness_check(now: Optional[datetime] = None) -> dict[str, Any]:
    current = now or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=ZoneInfo("UTC"))
    expected = latest_completed_us_session(current.astimezone(NY).date())
    stale = await stale_datasets(expected)
    if not stale:
        return {"status": "fresh", "expected_date": expected.isoformat()}

    async with async_session_maker() as db:
        alerted = set((await db.execute(
            select(DataFreshnessAlert.dataset).where(
                DataFreshnessAlert.expected_date == expected,
                DataFreshnessAlert.dataset.in_([item["dataset"] for item in stale]),
            )
        )).scalars())
    pending = [item for item in stale if item["dataset"] not in alerted]
    summary = {
        "expected_date": expected.isoformat(),
        "stale": [item["dataset"] for item in stale],
    }
    if not pending:
        return {"status": "already-alerted", **summary}

    delivered = await NotificationManager.broadcast(
        title=ALERT_TITLE,
        content=render_alert(expected, pending),
    )
    if not delivered:
        raise RuntimeError("Stale datasets were found but no notification channel accepted the alert")

    async with async_session_maker() as db, db.begin():
        db.add_all(
            DataFreshnessAlert(
                dataset=item["dataset"],
                expected_date=expected,
                published_date=item["published"],
                notified_at=utc_now(),
            )
            for item in pending
        )
    logger.warning("Stale-data alert sent for %s: %s", expected, [item["dataset"] for item in pending])
    return {"status": "alerted", "alerted": [item["dataset"] for item in pending], **summary}
