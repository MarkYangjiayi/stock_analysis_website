import os
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from database import async_session_maker
from models import DataFreshnessAlert
from services import freshness_alerts
from services.freshness_alerts import run_data_freshness_check, sessions_behind
from services.pipeline_runs import begin_pipeline_run, finish_pipeline_run, publish_datasets_and_finish

# Saturday 08:45 New York: the latest completed session is Friday 2026-10-16.
SATURDAY_MORNING = datetime(2026, 10, 17, 12, 45, tzinfo=timezone.utc)
OVERLAP = "Overlapping SP500 membership intervals for ARNC.US"


async def _publish(pipeline: str, datasets: list[str], as_of: date) -> None:
    run_id = await begin_pipeline_run(pipeline, as_of)
    async with async_session_maker() as db, db.begin():
        await publish_datasets_and_finish(db, datasets, as_of, run_id)


async def _fail(pipeline: str, target: date, error: str) -> None:
    run_id = await begin_pipeline_run(pipeline, target)
    await finish_pipeline_run(run_id, "failed", error_message=error)


async def _seed_october_outage() -> None:
    """The 2026-10-14..16 incident: membership failed, breadth and P/E deferred."""
    friday, last_good = date(2026, 10, 16), date(2026, 10, 13)
    await _publish("daily_screener", ["screener", "price_history"], friday)
    await _publish("rrg_price_history_backfill", ["rrg_price_history"], friday)
    await _publish("factor_cross_section", ["factors"], friday)
    await _publish("historical_universe_sync", ["universe_history"], last_good)
    await _publish("market_breadth", ["market_breadth"], last_good)
    await _publish("index_valuation", ["index_valuation"], last_good)
    for target in (date(2026, 10, 14), date(2026, 10, 15), friday):
        await _fail("historical_universe_sync", target, OVERLAP)


@pytest.fixture
def sent(monkeypatch):
    messages: list[dict] = []

    async def broadcast(title, content, channels=None, card=None):
        messages.append({"title": title, "content": content})
        return True

    monkeypatch.setattr(freshness_alerts.NotificationManager, "broadcast", broadcast)
    return messages


@pytest.mark.parametrize(
    ("published", "expected", "behind"),
    [
        (date(2026, 10, 13), date(2026, 10, 16), 3),
        (date(2026, 10, 16), date(2026, 10, 19), 1),   # weekend
        (date(2026, 11, 25), date(2026, 11, 27), 1),   # Thanksgiving
        (None, date(2026, 10, 16), None),
    ],
)
def test_sessions_behind_counts_market_sessions(published, expected, behind):
    assert sessions_behind(published, expected) == behind


@pytest.mark.asyncio
async def test_alert_names_each_stale_dataset_and_the_failure_behind_it(sent):
    await _seed_october_outage()

    result = await run_data_freshness_check(SATURDAY_MORNING)

    assert result["status"] == "alerted"
    assert result["expected_date"] == "2026-10-16"
    assert result["alerted"] == ["universe_history", "market_breadth", "index_valuation"]
    assert len(sent) == 1 and "Quantify" in sent[0]["title"]
    content = sent[0]["content"]
    assert "**应更新到 2026-10-16（最近完成的美股交易日），以下数据仍停在旧日期：**" in content
    assert "- **标普 500 历史成分**（`universe_history`）：最新 2026-10-13，落后 3 个交易日" in content
    assert f"最近一次运行（2026-10-16）失败：{OVERLAP}" in content
    assert "- **市场宽度**（`market_breadth`）：最新 2026-10-13，落后 3 个交易日" in content
    assert "此后没有新的运行（最近一次是 2026-10-13，published），通常是上游数据还没发布。" in content
    assert "筛选器快照" not in content and "因子截面" not in content


@pytest.mark.asyncio
async def test_each_dataset_is_reported_once_per_expected_session(sent):
    await _seed_october_outage()
    await run_data_freshness_check(SATURDAY_MORNING)

    again = await run_data_freshness_check(SATURDAY_MORNING)
    assert again["status"] == "already-alerted" and len(sent) == 1

    # Still stale on the next session: a new reminder.
    await _publish("daily_screener", ["screener", "price_history"], date(2026, 10, 19))
    tuesday = datetime(2026, 10, 20, 12, 45, tzinfo=timezone.utc)
    next_session = await run_data_freshness_check(tuesday)
    assert next_session["expected_date"] == "2026-10-19"
    assert "market_breadth" in next_session["alerted"] and len(sent) == 2
    assert "落后 4 个交易日" in sent[1]["content"]

    async with async_session_maker() as db:
        rows = (await db.execute(select(DataFreshnessAlert.dataset, DataFreshnessAlert.expected_date))).all()
    assert (("market_breadth", date(2026, 10, 16)) in rows) and (("market_breadth", date(2026, 10, 19)) in rows)


@pytest.mark.asyncio
async def test_undelivered_alert_is_retried_later(monkeypatch):
    await _seed_october_outage()

    async def failing_broadcast(title, content, channels=None, card=None):
        return False

    monkeypatch.setattr(freshness_alerts.NotificationManager, "broadcast", failing_broadcast)
    with pytest.raises(RuntimeError, match="no notification channel"):
        await run_data_freshness_check(SATURDAY_MORNING)
    async with async_session_maker() as db:
        assert (await db.execute(select(DataFreshnessAlert))).first() is None


@pytest.mark.asyncio
async def test_fresh_publications_send_nothing(sent):
    friday = date(2026, 10, 16)
    await _publish("daily_screener", ["screener", "price_history"], friday)
    await _publish("rrg_price_history_backfill", ["rrg_price_history"], friday)
    await _publish("historical_universe_sync", ["universe_history"], friday)
    await _publish("market_breadth", ["market_breadth"], friday)
    await _publish("factor_cross_section", ["factors"], friday)
    await _publish("index_valuation", ["index_valuation"], friday)

    assert (await run_data_freshness_check(SATURDAY_MORNING)) == {"status": "fresh", "expected_date": "2026-10-16"}
    assert sent == []


def test_theme_trends_are_watched_only_when_themes_run(monkeypatch):
    monkeypatch.setattr(freshness_alerts.settings, "THEMES_ENABLED", False)
    assert "theme_trends" not in {item.dataset for item in freshness_alerts.watched_datasets()}
    monkeypatch.setattr(freshness_alerts.settings, "THEMES_ENABLED", True)
    assert "theme_trends" in {item.dataset for item in freshness_alerts.watched_datasets()}


def test_scheduler_registers_the_check_only_when_enabled(monkeypatch):
    from core import scheduler
    calls = {}

    class FakeScheduler:
        running = True

        def add_job(self, job, trigger, **kwargs):
            calls[kwargs["id"]] = kwargs

    monkeypatch.setattr(scheduler, "scheduler", FakeScheduler())
    monkeypatch.setattr(scheduler.settings, "DATA_FRESHNESS_ALERTS_ENABLED", True)
    scheduler.start_scheduler()
    job = calls["daily_data_freshness_check"]
    assert (job["day_of_week"], job["hour"], job["minute"]) == ("tue-sat", 8, 45)
    calls.clear()
    monkeypatch.setattr(scheduler.settings, "DATA_FRESHNESS_ALERTS_ENABLED", False)
    scheduler.start_scheduler()
    assert "daily_data_freshness_check" not in calls


def test_migration_creates_and_drops_the_alert_table(tmp_path):
    path = tmp_path / "alerts.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
        db.execute("INSERT INTO alembic_version VALUES ('0025_expectation_signals')")
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{path}", "ENVIRONMENT": "test"}
    root = Path(__file__).resolve().parents[1]

    def alembic(*args):
        subprocess.run([sys.executable, "-m", "alembic", *args], cwd=root, env=env, check=True, capture_output=True)

    def tables():
        with sqlite3.connect(path) as db:
            return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    alembic("upgrade", "0026_data_freshness_alerts")
    assert "data_freshness_alerts" in tables()
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO data_freshness_alerts (dataset, expected_date, notified_at) VALUES ('market_breadth', '2026-10-16', '2026-10-17')")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO data_freshness_alerts (dataset, expected_date, notified_at) VALUES ('market_breadth', '2026-10-16', '2026-10-17')")
    alembic("downgrade", "0025_expectation_signals")
    assert "data_freshness_alerts" not in tables()
