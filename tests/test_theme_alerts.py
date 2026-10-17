import math
from datetime import date

import pytest
from sqlalchemy import select

from models import DataPublication, PipelineRun, Theme, ThemeAlert, ThemeDaily
from services.themes import alerts

AS_OF = date(2026, 10, 9)  # Friday
REFERENCE = date(2026, 10, 10)  # latest completed session -> AS_OF


async def _theme(db, slug: str, name_zh: str | None = None) -> Theme:
    theme = Theme(slug=slug, name_en=slug.title(), name_zh=name_zh or f"{slug}题材", megatrend=None,
                  definition="d", keywords=[], negative_keywords=[], proxy_etfs=[], seed_core=[], pins=[], bans=[],
                  status="active", taxonomy_version="themes-v1")
    db.add(theme)
    await db.flush()
    return theme


async def _run(db, as_of: date = AS_OF, *, status: str = "published", publish: bool = True) -> PipelineRun:
    run = PipelineRun(pipeline_name="theme_trends", target_date=as_of, status=status, stage=status,
                      version="theme-heat-v1")
    db.add(run)
    await db.flush()
    if publish:
        db.add(DataPublication(dataset="theme_trends", as_of_date=as_of, pipeline_run_id=run.id, status="published"))
    return run


def _day(run, theme, **values) -> ThemeDaily:
    session = values.pop("date", AS_OF)
    row = {"scoring_version": "theme-heat-v1", "pit": True, "rankable": True, "stage": "neutral",
           "candidate_stage": "neutral", "heat_now": 60.0, "x21": math.log(1.0842), "rank_now": 5}
    row.update(values)
    return ThemeDaily(source_run_id=run.id, theme_id=theme.id, date=session, **row)


async def _seed_events(db) -> dict:
    power = await _theme(db, "ai-power", "AI电力")
    quantum = await _theme(db, "quantum", "量子计算")
    neutral = await _theme(db, "neutral-theme")
    run = await _run(db)
    db.add_all([
        _day(run, power, stage="emerging", stage_prev="neutral", stage_changed=True, emerging_first_20=True,
             heat_now=72.44, rank_now=6, rank_change=9, leaders=["VST.US", "CEG.US", "TLN.US"]),
        _day(run, quantum, stage="crowded", stage_prev="leading", stage_changed=True, heat_now=91.0, rank_now=1,
             rank_change=0),
        _day(run, neutral, stage="neutral", rank_now=2),
    ])
    await db.commit()
    return {"power": power, "quantum": quantum, "run": run}


def _capture(monkeypatch, result: bool = True) -> list[dict]:
    messages = []

    async def fake(*, title, content, channels=None):  # no card_layout: the generic card
        messages.append({"title": title, "content": content})
        return result

    monkeypatch.setattr(alerts.NotificationManager, "broadcast", staticmethod(fake))
    return messages


@pytest.mark.asyncio
async def test_alerts_once_per_theme_type_and_data_date(db_session, monkeypatch):
    seeded = await _seed_events(db_session)
    messages = _capture(monkeypatch)

    first = await alerts.run_theme_alerts(REFERENCE)
    assert first == {"status": "alerted", "events": 2, "alerts": 2, "as_of_date": "2026-10-09"}
    assert messages[0]["title"] == alerts.ALERT_TITLE
    content = messages[0]["content"]
    assert content.startswith("**数据日期：2026-10-09｜题材生命周期**")
    assert "- AI电力（ai-power）：热度 72.4；排名 #6（↑9）；21日超额 +8.42%；龙头 VST、CEG、TLN" in content
    assert "- 量子计算（quantum）：主线→拥挤；热度 91.0；排名 #1（持平）" in content
    assert content.index("**萌芽（20个交易日内首次）**") < content.index("**进入拥挤 / 退潮（热度前10）**")
    assert "非交易建议" in content

    rows = (await db_session.execute(select(ThemeAlert).order_by(ThemeAlert.alert_type))).scalars().all()
    assert [(row.theme_id, row.alert_type, row.date) for row in rows] == [
        (seeded["power"].id, "emerging", AS_OF), (seeded["quantum"].id, "stage_change", AS_OF)]
    assert rows[0].payload["leaders"] == ["VST.US", "CEG.US", "TLN.US"]

    second = await alerts.run_theme_alerts(REFERENCE)
    assert second["status"] == "already-delivered"
    assert len(messages) == 1
    assert len((await db_session.execute(select(ThemeAlert))).scalars().all()) == 2


@pytest.mark.asyncio
async def test_attention_rerun_alerts_only_new_pairs(db_session, monkeypatch):
    """05:15 alerts the 04:50 run; the 07:50 re-run repoints the publication and alerts only new pairs."""
    seeded = await _seed_events(db_session)
    power, quantum = seeded["power"], seeded["quantum"]
    messages = _capture(monkeypatch)
    assert (await alerts.run_theme_alerts(REFERENCE))["alerts"] == 2

    grid = await _theme(db_session, "grid", "电网")
    rerun = await _run(db_session, publish=False)
    db_session.add_all([
        _day(rerun, power, stage="crowded", stage_prev="leading", stage_changed=True, rank_now=3),
        _day(rerun, quantum, stage="crowded", stage_prev="leading", stage_changed=True, rank_now=1),
        _day(rerun, grid, stage="fading", stage_prev="accelerating", stage_changed=True, rank_now=8),
    ])
    publication = (await db_session.execute(
        select(DataPublication).where(DataPublication.dataset == "theme_trends", DataPublication.as_of_date == AS_OF)
    )).scalar_one()
    publication.pipeline_run_id = rerun.id
    await db_session.commit()

    second = await alerts.run_theme_alerts(REFERENCE)
    assert (second["status"], second["alerts"]) == ("alerted", 2)
    content = messages[1]["content"]
    assert "- AI电力（ai-power）：主线→拥挤；" in content
    assert "- 电网（grid）：加速→退潮；" in content
    assert "quantum" not in content and "萌芽" not in content
    rows = (await db_session.execute(select(ThemeAlert))).scalars().all()
    assert sorted((row.theme_id, row.alert_type) for row in rows) == sorted([
        (power.id, "emerging"), (power.id, "stage_change"), (quantum.id, "stage_change"), (grid.id, "stage_change")])


@pytest.mark.asyncio
async def test_rejected_delivery_records_nothing(db_session, monkeypatch):
    await _seed_events(db_session)
    _capture(monkeypatch, result=False)
    with pytest.raises(RuntimeError, match="no notification channel"):
        await alerts.run_theme_alerts(REFERENCE)
    assert (await db_session.execute(select(ThemeAlert))).scalars().all() == []


@pytest.mark.asyncio
async def test_stale_missing_or_unpublished_publications_are_skipped(db_session, monkeypatch):
    messages = _capture(monkeypatch)
    assert (await alerts.run_theme_alerts(REFERENCE))["status"] == "skipped"

    theme = await _theme(db_session, "grid")
    old = await _run(db_session, date(2026, 10, 8))
    failed = await _run(db_session, AS_OF, status="failed", publish=False)
    db_session.add_all([
        _day(old, theme, date=date(2026, 10, 8), stage="emerging", stage_changed=True, emerging_first_20=True),
        _day(failed, theme, stage="emerging", stage_changed=True, emerging_first_20=True),
    ])
    await db_session.commit()
    result = await alerts.run_theme_alerts(REFERENCE)
    assert result["status"] == "skipped" and result["published_as_of"] == "2026-10-08"
    assert messages == []


@pytest.mark.asyncio
@pytest.mark.parametrize("sessions_ago,expected", [(5, "no-alerts"), (20, "no-alerts"), (21, "alerted"),
                                                   (25, "alerted")])
async def test_recent_emerging_alert_suppresses_reentry(db_session, monkeypatch, sessions_ago, expected):
    theme = await _theme(db_session, "ai-power")
    run = await _run(db_session)
    db_session.add(_day(run, theme, stage="emerging", stage_prev="neutral", stage_changed=True, emerging_first_20=True))
    earlier = alerts._sessions_through(AS_OF, sessions_ago + 1)[0]
    db_session.add(ThemeAlert(theme_id=theme.id, date=earlier, alert_type="emerging", payload={}))
    await db_session.commit()
    messages = _capture(monkeypatch)
    assert (await alerts.run_theme_alerts(REFERENCE))["status"] == expected
    assert len(messages) == (expected == "alerted")


@pytest.mark.asyncio
async def test_stage_alerts_need_top_ten_and_crowded_or_fading(db_session, monkeypatch):
    run = await _run(db_session)
    themes = [await _theme(db_session, slug) for slug in ("deep", "leader", "fade", "same")]
    db_session.add_all([
        _day(run, themes[0], stage="crowded", stage_prev="leading", stage_changed=True, rank_now=11),
        _day(run, themes[1], stage="leading", stage_prev="accelerating", stage_changed=True, rank_now=1),
        _day(run, themes[2], stage="fading", stage_prev="accelerating", stage_changed=False, rank_now=3),
        _day(run, themes[3], stage="fading", stage_prev="fading", stage_changed=True, rank_now=4),
    ])
    # Newly rankable themes start their stage series; that is not a lifecycle transition.
    db_session.add_all([
        _day(run, await _theme(db_session, "newly-rankable"), stage="crowded", stage_prev="insufficient",
             stage_changed=True, rank_now=2),
        _day(run, await _theme(db_session, "first-row"), stage="fading", stage_prev=None, stage_changed=True,
             rank_now=3),
    ])
    await db_session.commit()
    messages = _capture(monkeypatch)
    assert (await alerts.run_theme_alerts(REFERENCE))["status"] == "no-alerts"

    db_session.add(_day(run, await _theme(db_session, "fresh-fade"), stage="fading", stage_prev="accelerating",
                        stage_changed=True, rank_now=10, heat_now=None, x21=None, rank_change=None))
    await db_session.commit()
    assert (await alerts.run_theme_alerts(REFERENCE))["alerts"] == 1
    assert "加速→退潮；热度 —；排名 #10（—）；21日超额 —；龙头 —" in messages[0]["content"]
    assert "萌芽" not in messages[0]["content"]
