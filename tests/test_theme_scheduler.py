"""Theme job wiring in core.scheduler (spec §5) and the theme bootstrap."""

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest

from core.config import settings
from services.eodhd_client import EODHDQuotaExceeded
from services.themes import bootstrap


THEME_JOB_IDS = {
    "daily_theme_etf_prices",
    "daily_theme_news_sync",
    "daily_theme_trends_sync",
    "daily_theme_briefs",
    "daily_theme_alerts",
    "weekly_theme_proposals",
    "weekly_theme_membership_refresh",
    "theme_bootstrap",
}
# Tuesday 2026-10-06 04:50 ET; New York and UTC dates agree.
TUESDAY = date(2026, 10, 6)
MONDAY = date(2026, 10, 5)


class FakeScheduler:
    running = True

    def __init__(self):
        self.jobs = {}

    def add_job(self, job, trigger, **kwargs):
        self.jobs[kwargs["id"]] = {"job": job, "trigger": trigger, **kwargs}


def _fake_now(moment_utc: datetime):
    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment_utc.astimezone(tz) if tz is not None else moment_utc.replace(tzinfo=None)

    return FakeDatetime


@pytest.fixture
def sched(monkeypatch):
    from core import scheduler

    # Fresh locks per test so no lock is shared across event loops.
    for name in (
        "_theme_etf_prices_sync_lock",
        "_theme_news_sync_lock",
        "_theme_trends_sync_lock",
        "_theme_briefs_lock",
        "_theme_alerts_lock",
        "_theme_proposals_lock",
        "_theme_membership_sync_lock",
    ):
        monkeypatch.setattr(scheduler, name, asyncio.Lock())
    # Default clock: Tuesday 04:50 ET, before the briefs/alerts slots (tests override it).
    monkeypatch.setattr(scheduler, "datetime", _fake_now(datetime(2026, 10, 6, 8, 50, tzinfo=timezone.utc)))
    return scheduler


def _register(monkeypatch, sched, **flags):
    fake = FakeScheduler()
    monkeypatch.setattr(sched, "scheduler", fake)
    for name, value in flags.items():
        monkeypatch.setattr(settings, name, value)
    sched.start_scheduler()
    return fake.jobs


def test_theme_jobs_are_not_registered_when_disabled(monkeypatch, sched):
    jobs = _register(monkeypatch, sched, THEMES_ENABLED=False)
    assert not THEME_JOB_IDS & set(jobs)
    assert "daily_screener_sync" in jobs


def test_theme_jobs_registered_in_new_york_time_when_enabled(monkeypatch, sched):
    monkeypatch.setattr(sched, "datetime", _fake_now(datetime(2026, 10, 6, 8, 0, tzinfo=timezone.utc)))
    jobs = _register(
        monkeypatch, sched, THEMES_ENABLED=True, THEME_BRIEFS_ENABLED=True,
        THEME_ALERTS_ENABLED=True, THEME_PROPOSALS_ENABLED=True,
    )
    assert THEME_JOB_IDS <= set(jobs)

    def cron(job_id):
        job = jobs[job_id]
        assert job["trigger"] == "cron" and job["replace_existing"] is True
        return job["day_of_week"], job["hour"], job["minute"]

    assert cron("daily_theme_etf_prices") == ("tue-sat", 4, 35)
    assert cron("daily_theme_news_sync") == ("tue-sat", 4, 40)
    assert cron("daily_theme_trends_sync") == ("tue-sat", "4,7", 50)
    assert cron("daily_theme_briefs") == ("tue-sat", 5, 5)
    assert cron("daily_theme_alerts") == ("tue-sat", 5, 15)
    # After the 07:50 trends retry, so a late Saturday publication still gets that week's proposals.
    assert cron("weekly_theme_proposals") == ("sat", 8, 30)
    assert cron("weekly_theme_membership_refresh") == ("sun", 5, 0)
    assert jobs["daily_theme_trends_sync"]["job"] is sched.scheduled_theme_trends_sync

    bootstrap_job = jobs["theme_bootstrap"]
    assert bootstrap_job["trigger"] == "date" and bootstrap_job["job"] is sched.scheduled_theme_bootstrap
    run_date = bootstrap_job["run_date"]
    assert run_date.utcoffset() == timedelta(hours=-4)  # America/New_York (EDT)
    assert run_date - datetime(2026, 10, 6, 8, 0, tzinfo=timezone.utc) == timedelta(seconds=120)


def test_optional_theme_jobs_follow_their_flags(monkeypatch, sched):
    jobs = _register(
        monkeypatch, sched, THEMES_ENABLED=True, THEME_BRIEFS_ENABLED=False,
        THEME_ALERTS_ENABLED=False, THEME_PROPOSALS_ENABLED=False,
    )
    present = THEME_JOB_IDS & set(jobs)
    assert present == THEME_JOB_IDS - {"daily_theme_briefs", "daily_theme_alerts", "weekly_theme_proposals"}


def _should_not_run(name):
    async def fail(*args, **kwargs):
        raise AssertionError(f"{name} should not run")

    return fail


@pytest.mark.asyncio
async def test_publishing_theme_jobs_skip_when_target_is_published(monkeypatch, sched):
    async def latest(dataset):
        return MONDAY

    monkeypatch.setattr(sched, "latest_published_date", latest)
    for name in ("refresh_theme_etf_prices", "ingest_market_news", "refresh_theme_trends",
                 "sync_theme_taxonomy", "refresh_theme_membership"):
        monkeypatch.setattr(sched, name, _should_not_run(name))

    async def not_due(target):
        return False

    monkeypatch.setattr(sched, "theme_trends_rerun_due", not_due)
    skipped = {"status": "skipped", "reason": "already-published", "as_of_date": MONDAY.isoformat()}
    assert await sched.scheduled_theme_etf_prices_sync(TUESDAY) == skipped
    assert await sched.scheduled_theme_news_sync(TUESDAY) == skipped
    assert await sched.scheduled_theme_trends_sync(TUESDAY) == skipped
    # Sunday reference -> Friday as-of; a Friday membership publication is current.
    async def friday(dataset):
        return date(2026, 10, 9)

    monkeypatch.setattr(sched, "latest_published_date", friday)
    assert (await sched.scheduled_theme_membership_refresh(date(2026, 10, 11)))["status"] == "skipped"


@pytest.mark.asyncio
async def test_publishing_theme_jobs_run_for_unpublished_target(monkeypatch, sched):
    calls = []

    async def stale(dataset):
        return MONDAY - timedelta(days=3)

    def recorder(name):
        async def record(*args, **kwargs):
            calls.append((name, args, kwargs))
            return {"status": "published"}

        return record

    monkeypatch.setattr(sched, "latest_published_date", stale)
    for name in ("refresh_theme_etf_prices", "ingest_market_news", "refresh_theme_trends",
                 "sync_theme_taxonomy", "refresh_theme_membership"):
        monkeypatch.setattr(sched, name, recorder(name))
    monkeypatch.setattr(sched, "theme_trends_rerun_due", _should_not_run("theme_trends_rerun_due"))

    await sched.scheduled_theme_etf_prices_sync(TUESDAY)
    await sched.scheduled_theme_news_sync(TUESDAY)
    await sched.scheduled_theme_trends_sync(TUESDAY)
    await sched.scheduled_theme_membership_refresh(date(2026, 10, 11))
    assert calls == [
        ("refresh_theme_etf_prices", (MONDAY,), {}),
        ("ingest_market_news", (MONDAY,), {}),
        ("refresh_theme_trends", (MONDAY,), {}),
        ("sync_theme_taxonomy", (), {}),
        ("refresh_theme_membership", (date(2026, 10, 9),), {}),
    ]


@pytest.mark.asyncio
async def test_trends_guard_allows_the_attention_rerun(monkeypatch, sched):
    calls = []

    async def latest(dataset):
        return MONDAY

    async def due(target):
        calls.append(("due", target))
        return True

    async def refresh(target):
        calls.append(("refresh", target))
        return {"status": "published"}

    monkeypatch.setattr(sched, "latest_published_date", latest)
    monkeypatch.setattr(sched, "theme_trends_rerun_due", due)
    monkeypatch.setattr(sched, "refresh_theme_trends", refresh)
    assert (await sched.scheduled_theme_trends_sync(TUESDAY))["status"] == "published"
    assert calls == [("due", MONDAY), ("refresh", MONDAY)]

    # A newer publication never triggers a re-run of an older target.
    calls.clear()

    async def newer(dataset):
        return TUESDAY

    monkeypatch.setattr(sched, "latest_published_date", newer)
    assert (await sched.scheduled_theme_trends_sync(TUESDAY))["status"] == "skipped"
    assert calls == []


@pytest.mark.asyncio
async def test_briefs_alerts_and_proposals_wait_for_theme_trends(monkeypatch, sched):
    calls = []
    published = {"value": None}

    async def latest(dataset):
        assert dataset == "theme_trends"
        return published["value"]

    def recorder(name):
        async def record(*args, **kwargs):
            calls.append((name, args))
            return {"status": "done"}

        return record

    monkeypatch.setattr(sched, "latest_published_date", latest)
    for name in ("generate_theme_briefs", "run_theme_alerts", "refresh_theme_proposals"):
        monkeypatch.setattr(sched, name, recorder(name))

    deferred = {"status": "deferred", "reason": "theme-trends-not-published", "as_of_date": MONDAY.isoformat()}
    assert await sched.scheduled_theme_briefs(TUESDAY) == deferred
    assert await sched.scheduled_theme_alerts(TUESDAY) == deferred
    assert calls == []

    published["value"] = MONDAY
    await sched.scheduled_theme_briefs(TUESDAY)
    await sched.scheduled_theme_alerts(TUESDAY)
    # The cron call passes no reference date; alerts resolve their own session.
    monkeypatch.setattr(sched, "datetime", _fake_now(datetime(2026, 10, 6, 9, 15, tzinfo=timezone.utc)))
    await sched.scheduled_theme_alerts()
    published["value"] = date(2026, 10, 9)
    await sched.scheduled_theme_proposals(date(2026, 10, 10))  # Saturday -> Friday
    assert calls == [
        ("generate_theme_briefs", (MONDAY,)),
        ("run_theme_alerts", (TUESDAY,)),
        ("run_theme_alerts", (None,)),
        ("refresh_theme_proposals", (date(2026, 10, 9),)),
    ]


@pytest.mark.asyncio
async def test_theme_targets_are_computed_in_new_york_time(monkeypatch, sched):
    # 01:30 UTC Wednesday is still Tuesday evening in New York: target = Monday, not Tuesday.
    monkeypatch.setattr(sched, "datetime", _fake_now(datetime(2026, 10, 7, 1, 30, tzinfo=timezone.utc)))
    targets = []

    async def never(dataset):
        return None

    def recorder(name):
        async def record(target, *args, **kwargs):
            targets.append((name, target))
            return {"status": "published"}

        return record

    monkeypatch.setattr(sched, "latest_published_date", never)
    for name in ("refresh_theme_etf_prices", "ingest_market_news", "refresh_theme_trends"):
        monkeypatch.setattr(sched, name, recorder(name))
    await sched.scheduled_theme_etf_prices_sync()
    await sched.scheduled_theme_news_sync()
    await sched.scheduled_theme_trends_sync()
    assert targets == [
        ("refresh_theme_etf_prices", MONDAY),
        ("ingest_market_news", MONDAY),
        ("refresh_theme_trends", MONDAY),
    ]

    seen = {}

    async def fake_bootstrap(reference_date=None, **kwargs):
        seen["reference"] = reference_date
        seen["locked"] = [
            sched._theme_membership_sync_lock.locked(),
            sched._theme_etf_prices_sync_lock.locked(),
            sched._theme_news_sync_lock.locked(),
            sched._theme_trends_sync_lock.locked(),
        ]
        return {"status": "ok"}

    monkeypatch.setattr(sched, "bootstrap_themes", fake_bootstrap)
    assert await sched.scheduled_theme_bootstrap() == {"status": "ok"}
    assert seen == {"reference": TUESDAY, "locked": [True, True, True, True]}
    assert not sched._theme_trends_sync_lock.locked()


@pytest.mark.asyncio
async def test_cron_slot_waits_for_bootstrap_then_skips(monkeypatch, sched):
    order = []
    published = {"value": None}
    release = asyncio.Event()

    async def latest(dataset):
        return published["value"]

    async def slow_bootstrap(reference_date=None, **kwargs):
        order.append("bootstrap-start")
        await release.wait()
        published["value"] = MONDAY
        order.append("bootstrap-end")
        return {"status": "ok"}

    async def not_due(target):
        return False

    monkeypatch.setattr(sched, "latest_published_date", latest)
    monkeypatch.setattr(sched, "bootstrap_themes", slow_bootstrap)
    monkeypatch.setattr(sched, "theme_trends_rerun_due", not_due)
    monkeypatch.setattr(sched, "refresh_theme_trends", _should_not_run("refresh_theme_trends"))

    boot = asyncio.create_task(sched.scheduled_theme_bootstrap(TUESDAY))
    await asyncio.sleep(0)
    cron = asyncio.create_task(sched.scheduled_theme_trends_sync(TUESDAY))
    await asyncio.sleep(0)
    assert not cron.done()
    release.set()
    await boot
    assert (await cron)["status"] == "skipped"
    assert order == ["bootstrap-start", "bootstrap-end"]


# ---------------------------------------------------------------------------
# Briefs/alerts catch-up after a late theme_trends publication
# ---------------------------------------------------------------------------


def _et(hour, minute, day=TUESDAY):
    """An EDT wall-clock moment as UTC (EDT = UTC-4 in October)."""
    return datetime(day.year, day.month, day.day, hour + 4, minute, tzinfo=timezone.utc)


@pytest.fixture
def followups(monkeypatch, sched):
    monkeypatch.setattr(settings, "THEME_BRIEFS_ENABLED", True)
    monkeypatch.setattr(settings, "THEME_ALERTS_ENABLED", True)
    state = {"calls": [], "published": None, "fail": {}}

    async def latest(dataset):
        assert dataset == "theme_trends"
        return state["published"]

    async def refresh(target):
        state["calls"].append(("refresh_theme_trends", target))
        state["published"] = target
        return {"status": "published", "as_of_date": target.isoformat()}

    def recorder(name):
        async def record(*args, **kwargs):
            state["calls"].append((name, *args))
            if name in state["fail"]:
                raise state["fail"][name]
            return {"status": "done"}

        return record

    async def not_due(target):
        return False

    monkeypatch.setattr(sched, "latest_published_date", latest)
    monkeypatch.setattr(sched, "refresh_theme_trends", refresh)
    monkeypatch.setattr(sched, "theme_trends_rerun_due", not_due)
    for name in ("generate_theme_briefs", "run_theme_alerts"):
        monkeypatch.setattr(sched, name, recorder(name))

    def at(moment):
        monkeypatch.setattr(sched, "datetime", _fake_now(moment))

    state["at"] = at
    return state


@pytest.mark.asyncio
async def test_alerts_wait_for_a_pending_attention_rerun(monkeypatch, sched):
    calls = []

    async def latest(dataset):
        return MONDAY

    async def rerun_due(target):
        calls.append(("rerun_due", target))
        return True

    async def alerts(*args, **kwargs):
        calls.append(("alerts", args))
        return {"status": "alerted"}

    monkeypatch.setattr(sched, "latest_published_date", latest)
    monkeypatch.setattr(sched, "theme_trends_rerun_due", rerun_due)
    monkeypatch.setattr(sched, "run_theme_alerts", alerts)

    result = await sched.scheduled_theme_alerts(TUESDAY)

    assert result == {"status": "deferred", "reason": "theme-trends-rerun-pending", "as_of_date": MONDAY.isoformat()}
    assert calls == [("rerun_due", MONDAY)]


@pytest.mark.asyncio
async def test_trends_first_published_at_0750_runs_the_missed_briefs_and_alerts(followups, sched):
    followups["at"](_et(5, 5))
    assert (await sched.scheduled_theme_briefs())["status"] == "deferred"
    followups["at"](_et(5, 15))
    assert (await sched.scheduled_theme_alerts())["status"] == "deferred"
    assert followups["calls"] == []

    followups["at"](_et(7, 50))
    result = await sched.scheduled_theme_trends_sync()

    assert result["status"] == "published"
    assert result["theme_briefs"] == {"status": "done"} and result["theme_alerts"] == {"status": "done"}
    assert followups["calls"] == [
        ("refresh_theme_trends", MONDAY),
        ("generate_theme_briefs", MONDAY),
        ("run_theme_alerts", None),  # the cron passes no reference; alerts resolve their session
    ]


@pytest.mark.asyncio
async def test_trends_published_at_0450_leave_briefs_and_alerts_to_their_slots(followups, sched):
    followups["at"](_et(4, 50))
    result = await sched.scheduled_theme_trends_sync()
    assert result == {"status": "published", "as_of_date": MONDAY.isoformat()}
    assert followups["calls"] == [("refresh_theme_trends", MONDAY)]

    # Between the two slots only briefs have been missed.
    followups["calls"].clear()
    followups["published"] = None
    followups["at"](_et(5, 10))
    result = await sched.scheduled_theme_trends_sync()
    assert "theme_briefs" in result and "theme_alerts" not in result
    assert [call[0] for call in followups["calls"]] == ["refresh_theme_trends", "generate_theme_briefs"]


@pytest.mark.asyncio
async def test_0750_slot_catches_up_after_a_bootstrap_that_held_the_trends_lock(followups, sched, monkeypatch):
    # The bootstrap published Monday's trends after 05:15 (briefs/alerts had deferred);
    # the 07:50 trends slot skips the refresh but still runs both.
    followups["published"] = MONDAY
    monkeypatch.setattr(sched, "refresh_theme_trends", _should_not_run("refresh_theme_trends"))
    followups["at"](_et(7, 50))
    result = await sched.scheduled_theme_trends_sync()
    assert result["status"] == "skipped"
    assert [call[0] for call in followups["calls"]] == ["generate_theme_briefs", "run_theme_alerts"]

    # Disabled jobs are not caught up; a deferred trends run never triggers them.
    followups["calls"].clear()
    monkeypatch.setattr(settings, "THEME_BRIEFS_ENABLED", False)
    monkeypatch.setattr(settings, "THEME_ALERTS_ENABLED", False)
    assert (await sched.scheduled_theme_trends_sync())["status"] == "skipped"
    assert followups["calls"] == []

    monkeypatch.setattr(settings, "THEME_BRIEFS_ENABLED", True)
    followups["published"] = None

    async def deferred(target):
        return {"status": "deferred", "reason": "missing-publications"}

    monkeypatch.setattr(sched, "refresh_theme_trends", deferred)
    assert await sched.scheduled_theme_trends_sync() == {"status": "deferred", "reason": "missing-publications"}
    assert followups["calls"] == []


@pytest.mark.asyncio
async def test_catch_up_failure_is_reported_not_raised(followups, sched, caplog):
    followups["fail"] = {"run_theme_alerts": RuntimeError("webhook https://hooks.example/SECRET rejected")}
    followups["at"](_et(7, 50))
    result = await sched.scheduled_theme_trends_sync()
    assert result["status"] == "published" and result["theme_briefs"] == {"status": "done"}
    assert result["theme_alerts"] == {
        "status": "failed", "reason": "RuntimeError", "as_of_date": MONDAY.isoformat(),
    }
    assert "SECRET" not in caplog.text and "RuntimeError" in caplog.text



@pytest.mark.asyncio
async def test_bootstrap_publishing_after_the_slots_runs_the_missed_briefs_and_alerts(followups, sched, monkeypatch):
    outcome = {"theme_trends": "published"}

    async def fake_bootstrap(reference_date=None, **kwargs):
        assert sched._theme_trends_sync_lock.locked()
        followups["calls"].append(("bootstrap_themes", reference_date))
        followups["published"] = MONDAY if outcome["theme_trends"] in {"published", "current"} else None
        return dict(outcome)

    monkeypatch.setattr(sched, "bootstrap_themes", fake_bootstrap)

    # A worker that recovers at 09:02: the 04:50/07:50 trends slots and both follow-up slots are gone.
    followups["at"](_et(9, 2))
    result = await sched.scheduled_theme_bootstrap()
    assert result["theme_briefs"] == {"status": "done"} and result["theme_alerts"] == {"status": "done"}
    assert followups["calls"] == [
        ("bootstrap_themes", TUESDAY),
        ("generate_theme_briefs", MONDAY),
        ("run_theme_alerts", None),
    ]
    assert not sched._theme_trends_sync_lock.locked()

    # Before the 05:05/05:15 slots their own jobs run them.
    followups["calls"].clear()
    followups["at"](_et(4, 52))
    assert "theme_briefs" not in await sched.scheduled_theme_bootstrap()
    assert [call[0] for call in followups["calls"]] == ["bootstrap_themes"]

    # Nothing published: nothing to catch up.
    for status in ("deferred", "failed"):
        followups["calls"].clear()
        outcome["theme_trends"] = status
        followups["at"](_et(9, 2))
        assert await sched.scheduled_theme_bootstrap() == {"theme_trends": status}
        assert [call[0] for call in followups["calls"]] == ["bootstrap_themes"]

def test_theme_slot_passed_uses_the_first_slot_day_after_the_target(sched):
    friday = date(2026, 10, 9)
    saturday = date(2026, 10, 10)
    assert not sched._theme_slot_passed(friday, sched.THEME_BRIEFS_SLOT, sched.ny_tz.localize(
        datetime(2026, 10, 10, 5, 4)))
    assert sched._theme_slot_passed(friday, sched.THEME_BRIEFS_SLOT, sched.ny_tz.localize(
        datetime(2026, 10, 10, 5, 5)))
    assert not sched._theme_slot_passed(friday, sched.THEME_ALERTS_SLOT, sched.ny_tz.localize(
        datetime(2026, 10, 10, 5, 10)))
    # A manual Monday run for Friday's session: Saturday's slots are long past.
    assert sched._theme_slot_passed(friday, sched.THEME_ALERTS_SLOT, sched.ny_tz.localize(
        datetime(2026, 10, 12, 1, 0)))
    assert saturday.weekday() in sched.THEME_SLOT_WEEKDAYS


# ---------------------------------------------------------------------------
# bootstrap_themes
# ---------------------------------------------------------------------------


@pytest.fixture
def boot(monkeypatch):
    monkeypatch.setattr(settings, "THEMES_ENABLED", True)
    monkeypatch.setattr(settings, "THEME_MEMBERSHIP_MAX_AGE_DAYS", 8)
    state = {"calls": [], "published": {}, "rerun_due": False, "fail": {}}

    async def latest(dataset):
        return state["published"].get(dataset)

    def recorder(name, status="published"):
        async def record(*args, **kwargs):
            state["calls"].append((name, args, kwargs))
            if name in state["fail"]:
                raise state["fail"][name]
            return {"status": status, "version": "test-v1"}

        return record

    async def rerun_due(target):
        return state["rerun_due"]

    monkeypatch.setattr(bootstrap, "latest_published_date", latest)
    monkeypatch.setattr(bootstrap, "theme_trends_rerun_due", rerun_due)
    for name in ("sync_theme_taxonomy", "refresh_theme_membership", "refresh_theme_etf_prices",
                 "ingest_market_news", "refresh_theme_trends"):
        monkeypatch.setattr(bootstrap, name, recorder(name))

    async def no_alerts(*args, **kwargs):
        state["calls"].append(("alert", args, kwargs))

    from services.notifications import NotificationManager
    from services.themes import alerts

    monkeypatch.setattr(NotificationManager, "broadcast", no_alerts)
    monkeypatch.setattr(alerts, "run_theme_alerts", no_alerts)
    return state


@pytest.mark.asyncio
async def test_bootstrap_cold_start_runs_every_step_in_order(boot):
    result = await bootstrap.bootstrap_themes(TUESDAY)

    assert [name for name, _, _ in boot["calls"]] == [
        "sync_theme_taxonomy",
        "refresh_theme_membership",
        "refresh_theme_etf_prices",
        "ingest_market_news",
        "refresh_theme_trends",
    ]
    assert all(args == (MONDAY,) for name, args, _ in boot["calls"][1:])
    assert boot["calls"][3][2] == {"max_backfill_days": None}
    assert result == {
        "target_date": MONDAY.isoformat(),
        "taxonomy": "synced",
        "taxonomy_version": "test-v1",
        "theme_membership": "published",
        "theme_etf_prices": "published",
        "news_attention": "published",
        "theme_trends": "published",
    }


@pytest.mark.asyncio
async def test_bootstrap_skips_current_publications(boot):
    boot["published"] = {
        "theme_membership": MONDAY - timedelta(days=8),
        "theme_etf_prices": MONDAY,
        "news_attention": MONDAY,
        "theme_trends": MONDAY,
    }
    result = await bootstrap.bootstrap_themes(TUESDAY, max_news_backfill_days=10)
    assert [name for name, _, _ in boot["calls"]] == ["sync_theme_taxonomy"]
    assert (result["theme_membership"], result["theme_etf_prices"], result["news_attention"],
            result["theme_trends"]) == ("current", "current", "current", "current")

    # A degraded publication due its re-run is refreshed; stale membership is rebuilt.
    boot["calls"].clear()
    boot["rerun_due"] = True
    boot["published"]["theme_membership"] = MONDAY - timedelta(days=9)
    result = await bootstrap.bootstrap_themes(TUESDAY)
    assert [name for name, _, _ in boot["calls"]] == [
        "sync_theme_taxonomy", "refresh_theme_membership", "refresh_theme_trends",
    ]


@pytest.mark.asyncio
async def test_bootstrap_continues_after_failures_and_never_alerts(boot, caplog):
    boot["fail"] = {
        "refresh_theme_membership": RuntimeError("membership boom"),
        "ingest_market_news": EODHDQuotaExceeded("quota https://eodhd.example/api?api_token=SECRET"),
    }
    result = await bootstrap.bootstrap_themes(TUESDAY)
    assert result["theme_membership"] == "failed"
    assert result["news_attention"] == "failed"
    assert result["theme_etf_prices"] == "published" and result["theme_trends"] == "published"
    assert not any(name == "alert" for name, _, _ in boot["calls"])
    assert "SECRET" not in caplog.text and "EODHDQuotaExceeded" in caplog.text


@pytest.mark.asyncio
async def test_bootstrap_is_disabled_with_the_master_switch(boot, monkeypatch):
    monkeypatch.setattr(settings, "THEMES_ENABLED", False)
    result = await bootstrap.bootstrap_themes(TUESDAY)
    assert result["status"] == "disabled" and boot["calls"] == []


@pytest.mark.asyncio
async def test_bootstrap_defaults_to_the_new_york_date(boot, monkeypatch):
    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 7, 1, 30, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(bootstrap, "datetime", FakeDatetime)
    result = await bootstrap.bootstrap_themes()
    assert result["target_date"] == MONDAY.isoformat()
