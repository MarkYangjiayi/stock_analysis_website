"""Deduplicated Feishu theme lifecycle alerts.

Mirrors ``rsi_monitor``: one Chinese digest per run on the generic card, alert
rows keyed by the data date and recorded only after every channel accepted the
message (at-least-once delivery). Only a publication for the latest completed
session is alerted, so a late or failed pipeline never pushes old events.
Repeated calls for the same session are safe (a later slot after a re-run or a
late publication sends only events not yet delivered for that data date).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from core.time_utils import utc_now
from core.trading_calendar import NEW_YORK, is_us_market_session, latest_completed_us_session
from database import async_session_maker
from models import Theme, ThemeAlert, ThemeDaily
from services.notifications import NotificationManager
from services.report_renderer import compact, display_ticker, theme_level, theme_rank_change, theme_stage, value
from services.report_market import number
from services.themes.briefs import published_theme_run

logger = logging.getLogger(__name__)

ALERT_TITLE = "🧭 Quantify 题材阶段提醒"
EMERGING_ALERT = "emerging"
STAGE_CHANGE_ALERT = "stage_change"
EMERGING_REPEAT_SESSIONS = 20
STAGE_ALERT_TOP_N = 10
STAGE_ALERT_TARGETS = frozenset({"crowded", "fading"})


@dataclass(frozen=True)
class ThemeEvent:
    theme_id: int
    slug: str
    name_zh: str
    alert_type: str
    as_of: date
    stage: str
    stage_prev: Optional[str]
    rank_now: Optional[int]
    rank_change: Optional[int]
    heat_now: Optional[float]
    excess_21d_pct: Optional[float]
    leaders: tuple[str, ...] = field(default_factory=tuple)

    def payload(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name_zh": self.name_zh,
            "stage": self.stage,
            "stage_prev": self.stage_prev,
            "rank_now": self.rank_now,
            "rank_change": self.rank_change,
            "heat_now": self.heat_now,
            "excess_21d_pct": self.excess_21d_pct,
            "leaders": list(self.leaders),
        }


def _sessions_through(target: date, count: int) -> list[date]:
    sessions: list[date] = []
    cursor = target
    while len(sessions) < count:
        if is_us_market_session(cursor):
            sessions.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(sessions))


def _leaders(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    tickers = []
    for item in raw:
        ticker = item.get("ticker") if isinstance(item, dict) else item
        if isinstance(ticker, str) and ticker:
            tickers.append(ticker)
    return tuple(tickers[:3])


def _excess_pct(raw: Any) -> Optional[float]:
    parsed = number(raw)
    if parsed is None:
        return None
    try:
        return number(round(math.expm1(parsed) * 100, 2))
    except OverflowError:
        return None


def _event(row: ThemeDaily, theme: Theme, alert_type: str) -> ThemeEvent:
    heat = number(row.heat_now)
    return ThemeEvent(
        theme_id=theme.id,
        slug=theme.slug,
        name_zh=theme.name_zh or theme.name_en or theme.slug,
        alert_type=alert_type,
        as_of=row.date,
        stage=row.stage,
        stage_prev=row.stage_prev,
        rank_now=row.rank_now,
        rank_change=row.rank_change,
        heat_now=round(heat, 1) if heat is not None else None,
        excess_21d_pct=_excess_pct(row.x21),
        leaders=_leaders(row.leaders),
    )


def theme_events(rows: list[tuple[ThemeDaily, Theme]], *, recently_emerged: set[int]) -> list[ThemeEvent]:
    """Emerging (first in 20 sessions) plus top-10 themes whose stage changed into crowded/fading.

    A theme that just became rankable ("insufficient" → anything) has no lifecycle
    transition to report; like the morning report, those moves are left out.
    """
    ordered = sorted(rows, key=lambda pair: (pair[0].rank_now is None, pair[0].rank_now or 0, pair[1].slug))
    events = [
        _event(row, theme, EMERGING_ALERT)
        for row, theme in ordered
        if row.emerging_first_20 and theme.id not in recently_emerged
    ]
    events.extend(
        _event(row, theme, STAGE_CHANGE_ALERT)
        for row, theme in ordered
        if row.stage_changed
        and row.stage in STAGE_ALERT_TARGETS
        and row.stage_prev
        and row.stage_prev != row.stage
        and row.stage_prev != "insufficient"
        and row.rankable
        and row.rank_now is not None
        and row.rank_now <= STAGE_ALERT_TOP_N
    )
    return events


async def _recently_emerged(target: date) -> set[int]:
    """Themes with an emerging alert in the 20 sessions before ``target``."""
    window_start = _sessions_through(target, EMERGING_REPEAT_SESSIONS + 1)[0]
    async with async_session_maker() as db:
        result = await db.execute(
            select(ThemeAlert.theme_id).where(
                ThemeAlert.alert_type == EMERGING_ALERT,
                ThemeAlert.date >= window_start,
                ThemeAlert.date < target,
            )
        )
        return set(result.scalars())


async def _not_yet_delivered(events: list[ThemeEvent]) -> list[ThemeEvent]:
    if not events:
        return []
    target = events[0].as_of
    async with async_session_maker() as db:
        result = await db.execute(
            select(ThemeAlert.theme_id, ThemeAlert.alert_type).where(
                ThemeAlert.date == target,
                ThemeAlert.theme_id.in_({event.theme_id for event in events}),
            )
        )
        delivered = set(result.all())
    return [event for event in events if (event.theme_id, event.alert_type) not in delivered]


def _event_line(event: ThemeEvent, *, transition: bool) -> str:
    leaders = "、".join(display_ticker(ticker) for ticker in event.leaders) or "—"
    rank = f"#{event.rank_now}" if event.rank_now is not None else "未排名"
    parts = [f"- {compact(event.name_zh)}（{event.slug}）："]
    if transition:
        parts.append(f"{theme_stage(event.stage_prev)}→{theme_stage(event.stage)}；")
    parts.append(f"热度 {theme_level(event.heat_now)}；排名 {rank}（{theme_rank_change(event.rank_change)}）；"
                 f"21日超额 {value(event.excess_21d_pct, '%', True)}；龙头 {leaders}")
    return "".join(parts)


def notification_content(events: list[ThemeEvent]) -> str:
    emerging = [event for event in events if event.alert_type == EMERGING_ALERT]
    changes = [event for event in events if event.alert_type == STAGE_CHANGE_ALERT]
    lines = [f"**数据日期：{events[0].as_of.isoformat()}｜题材生命周期**"]
    if emerging:
        lines.extend(["", f"**萌芽（{EMERGING_REPEAT_SESSIONS}个交易日内首次）**"])
        lines.extend(_event_line(event, transition=False) for event in emerging)
    if changes:
        lines.extend(["", f"**进入拥挤 / 退潮（热度前{STAGE_ALERT_TOP_N}）**"])
        lines.extend(_event_line(event, transition=True) for event in changes)
    lines.extend(["", "阶段由已发布题材数据按公式确定性计算，非交易建议；新闻与涨跌的因果关系未经验证。"])
    return "\n".join(lines)


async def run_theme_alerts(reference_date: Optional[date] = None) -> dict:
    target = latest_completed_us_session(reference_date or datetime.now(NEW_YORK).date())
    base = {"as_of_date": target.isoformat()}
    async with async_session_maker() as db:
        publication = await published_theme_run(db, on_or_before=target)
        if publication is None or publication[0] != target:
            return {
                "status": "skipped",
                "reason": "theme-trends-not-published",
                "published_as_of": publication[0].isoformat() if publication else None,
                **base,
            }
        as_of, run_id = publication
        rows = [
            (row, theme)
            for row, theme in (await db.execute(
                select(ThemeDaily, Theme)
                .join(Theme, Theme.id == ThemeDaily.theme_id)
                .where(ThemeDaily.source_run_id == run_id, ThemeDaily.date == as_of, Theme.status != "retired")
            )).all()
        ]
    events = theme_events(rows, recently_emerged=await _recently_emerged(target))
    pending = await _not_yet_delivered(events)
    if not pending:
        return {"status": "no-alerts" if not events else "already-delivered", "events": len(events), **base}

    delivered = await NotificationManager.broadcast(title=ALERT_TITLE, content=notification_content(pending))
    if not delivered:
        raise RuntimeError("Theme lifecycle events were detected but no notification channel accepted the alert")

    notified_at = utc_now()
    async with async_session_maker() as db, db.begin():
        await db.execute(
            insert(ThemeAlert)
            .values([
                {
                    "theme_id": event.theme_id,
                    "date": event.as_of,
                    "alert_type": event.alert_type,
                    "payload": event.payload(),
                    "notified_at": notified_at,
                }
                for event in pending
            ])
            .on_conflict_do_nothing(index_elements=["theme_id", "alert_type", "date"])
        )
    return {"status": "alerted", "events": len(events), "alerts": len(pending), **base}
