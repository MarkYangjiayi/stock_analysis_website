import math
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import delete, select, update

from api.schemas import (
    ThemeDetailResponse,
    ThemeHistoryResponse,
    ThemeListResponse,
    ThemeProposalDecisionResponse,
    ThemeProposalsResponse,
    TrendingCompaniesResponse,
)
from core.config import settings
from core.trading_calendar import is_us_market_session
from models import (
    CompanyTrendDaily,
    DailyPrice,
    DataPublication,
    PipelineRun,
    StockScreenerSnapshot,
    Theme,
    ThemeDaily,
    ThemeMember,
    ThemeMemberDaily,
    ThemeProposal,
    Ticker,
)
from services import deepseek_client, eodhd_client
from services.themes import serving
from services.themes.pipeline import THEME_TRENDS_DATASET
from services.themes.serving import (
    ThemeNotFound,
    ThemeProposalNotFound,
    ThemeTrendsDateUnavailable,
    ThemeTrendsUnavailable,
    decide_theme_proposal,
    get_theme_detail,
    get_theme_history,
    get_theme_list,
    get_trending_companies,
    list_theme_proposals,
)
from services.themes.taxonomy import parse_taxonomy

ADMIN = {"X-API-Key": "test-secret"}
AS_OF = date(2026, 10, 9)  # Friday
THEME_GETS = (
    "/api/v1/themes",
    "/api/v1/themes/ai-compute",
    "/api/v1/themes/ai-compute/history",
    "/api/v1/trending-companies",
)


def _sessions(end: date, count: int) -> list[date]:
    sessions = []
    current = end
    while len(sessions) < count:
        if is_us_market_session(current):
            sessions.append(current)
        current -= timedelta(days=1)
    return sessions[::-1]


async def _add_theme(db, slug: str, *, status: str = "active", proxy_etfs=(), **values) -> Theme:
    theme = Theme(
        slug=slug,
        name_en=values.pop("name_en", slug.replace("-", " ").title()),
        name_zh=values.pop("name_zh", f"{slug} 中文"),
        megatrend="ai-infrastructure",
        definition=f"{slug} definition",
        keywords=["alpha", "beta"],
        negative_keywords=[],
        proxy_etfs=list(proxy_etfs),
        seed_core=[],
        pins=[],
        bans=[],
        status=status,
        taxonomy_version="themes-v1",
        **values,
    )
    db.add(theme)
    await db.flush()
    return theme


async def _add_run(db, target: date, *, status: str = "published", quality_report=None,
                   pipeline_name: str = "theme_trends") -> PipelineRun:
    run = PipelineRun(
        pipeline_name=pipeline_name,
        target_date=target,
        status=status,
        stage=status,
        version="theme-heat-v1",
        quality_report=quality_report,
    )
    db.add(run)
    await db.flush()
    return run


def _publish(db, run: PipelineRun, as_of: date, dataset: str = THEME_TRENDS_DATASET) -> None:
    db.add(DataPublication(dataset=dataset, as_of_date=as_of, pipeline_run_id=run.id, status="published"))


def _theme_day(run: PipelineRun, theme: Theme, session: date, **values) -> ThemeDaily:
    row = {
        "scoring_version": "theme-heat-v1",
        "pit": True,
        "rankable": True,
        "n_members": 8,
        "n_priced": 8,
        "stage": "neutral",
        "candidate_stage": "neutral",
        "heat": 50.0,
        "heat_now": 50.0,
        "heat_trend": 50.0,
        "basket_index": 1.0,
        "ret_1d": 0.0,
    }
    row.update(values)
    return ThemeDaily(source_run_id=run.id, theme_id=theme.id, date=session, **row)


async def _seed_list_publication(db, *, quality_report=None) -> dict:
    """One daily publication at AS_OF plus two earlier backfilled dates for sparklines."""
    previous = _sessions(AS_OF, 3)[:2]
    themes = {
        "ai-compute": await _add_theme(db, "ai-compute", name_en="AI Compute", proxy_etfs=["SMH.US"]),
        "memory-storage": await _add_theme(db, "memory-storage"),
        "quantum": await _add_theme(db, "quantum"),
        "tiny-theme": await _add_theme(db, "tiny-theme"),
        "old-theme": await _add_theme(db, "old-theme", status="retired"),
    }
    backfill = await _add_run(db, previous[-1])
    run = await _add_run(db, AS_OF, quality_report=quality_report)
    superseded = await _add_run(db, AS_OF, status="failed")
    for session in previous:
        _publish(db, backfill, session)
    _publish(db, run, AS_OF)
    db.add_all([
        _theme_day(backfill, themes["ai-compute"], previous[0], heat_now=60.0, pit=False),
        _theme_day(backfill, themes["ai-compute"], previous[1], heat_now=70.0, pit=False),
        # Rows of a run that is not the date's publication are never served.
        _theme_day(superseded, themes["ai-compute"], AS_OF, heat_now=10.0, rank_now=9, pit=False),
        _theme_day(
            run, themes["ai-compute"], AS_OF,
            stage="leading", candidate_stage="crowded", stage_changed=True,
            heat=82.0, heat_now=80.0, heat_trend=75.0,
            rank_now=2, rank_trend=1, rank_change=3, streak_top5=4,
            score_price=1.2, score_breadth=0.8, score_volume=0.4, score_attention=None,
            x5=math.log(1.05), x21=math.log(1.10), x63=math.log(0.90),
            b50=0.7, nh=0.4, d_b50=0.12, ign=2, ign5=5, n_members=12, n_priced=11,
            leaders=["NVDA.US", "AMD.US"], proxy_etf="SMH.US", proxy_x21=math.log(1.2),
            attention_degraded=True,
        ),
        _theme_day(
            run, themes["memory-storage"], AS_OF,
            stage="emerging", candidate_stage="emerging", emerge=True,
            heat_now=85.0, rank_now=1, rank_trend=2, rank_change=1,
            leaders=[{"ticker": "MU.US", "leader_score": 0.9}],
        ),
        _theme_day(
            run, themes["quantum"], AS_OF,
            stage="fading", candidate_stage="fading", attention_only=True,
            heat_now=40.0, rank_now=3, rank_trend=3, rank_change=-1,
        ),
        _theme_day(
            run, themes["tiny-theme"], AS_OF,
            rankable=False, stage="insufficient", candidate_stage="insufficient",
            heat=None, heat_now=None, heat_trend=None, n_members=2, n_priced=2,
        ),
        _theme_day(run, themes["old-theme"], AS_OF, rank_now=None, rankable=False,
                   stage="insufficient", candidate_stage="insufficient"),
    ])
    db.add_all([Ticker(ticker="NVDA.US", name="NVIDIA"), Ticker(ticker="MU.US", name="Micron")])
    await db.commit()
    return {"themes": themes, "run": run, "previous": previous}


async def _seed_history_publication(db) -> dict:
    """A backfill run (s0..s3), two daily runs (s4, s5), a superseded and a failed run."""
    sessions = _sessions(AS_OF, 7)
    s = sessions
    theme = await _add_theme(db, "ai-compute", proxy_etfs=["SMH.US", "AIQ.US"])
    await _add_theme(db, "orphan-theme")
    await _add_theme(db, "old-theme", status="retired")
    backfill = await _add_run(db, s[3])
    daily_4 = await _add_run(db, s[4])
    daily_5 = await _add_run(db, s[5])
    superseded_5 = await _add_run(db, s[5])
    failed_6 = await _add_run(db, s[6], status="failed")
    for session in s[:4]:
        _publish(db, backfill, session)
    _publish(db, daily_4, s[4])
    _publish(db, daily_5, s[5])
    _publish(db, failed_6, s[6])
    # The backfill run's own index is consistent; its ret_1d is deliberately absent so
    # same-run steps must use the index ratio. Daily runs restart their index base.
    backfill_index = [1.0, 1.1, 1.21, 1.331]
    for index, session in enumerate(s[:4]):
        db.add(_theme_day(
            backfill, theme, session,
            basket_index=backfill_index[index], ret_1d=None,
            heat=40.0 + index, heat_now=41.0 + index, heat_trend=42.0 + index,
            score_price=0.1 * index, b50=0.5, nh=0.2, ign=index,
            stage="neutral", rank_now=5 - index, pit=index >= 2,
        ))
    db.add_all([
        _theme_day(daily_4, theme, s[4], basket_index=5.0, ret_1d=0.10, heat=60.0, ign=4,
                   stage="accelerating", candidate_stage="accelerating", rank_now=2, proxy_etf="SMH.US"),
        _theme_day(daily_5, theme, s[5], basket_index=9.0, ret_1d=-0.50, heat=70.0, ign=1,
                   stage="leading", candidate_stage="leading", rank_now=1, proxy_etf="SMH.US",
                   x5=math.log(1.05), leaders=["AMD.US", "NVDA.US"]),
        _theme_day(superseded_5, theme, s[5], basket_index=1.0, ret_1d=0.0, heat=1.0, rank_now=9),
        _theme_day(failed_6, theme, s[6], heat=99.0, rank_now=1),
    ])

    db.add_all([Ticker(ticker=ticker) for ticker in ("SPY.US", "SMH.US")])
    db.add_all([
        Ticker(ticker="NVDA.US", name="NVIDIA"),
        Ticker(ticker="AMD.US", name="Advanced Micro Devices"),
    ])
    await db.flush()
    for index, session in enumerate(s):
        db.add(DailyPrice(ticker="SPY.US", date=session, adjusted_close=100 + 2 * index, close=500))
        if index == 0:
            continue  # the proxy ETF has no bar on the first returned date
        adjusted = None if index == 3 else 50 + 5 * (index - 1)
        db.add(DailyPrice(ticker="SMH.US", date=session, adjusted_close=adjusted, close=60))

    screener_run = await _add_run(db, s[5], pipeline_name="screener")
    _publish(db, screener_run, s[5], dataset="screener")
    db.add(StockScreenerSnapshot(ticker="NVDA.US", date=s[5], name="NVIDIA Corporation"))

    member = {"theme_id": theme.id, "source_run_id": daily_5.id, "date": s[5]}
    db.add_all([
        ThemeMemberDaily(**member, ticker="NVDA.US", tier="core", weight=0.30, close=120.5, r1=0.02,
                         x5=math.log(1.10), x21=math.log(1.25), pth=0.97, rvol5=math.log(3.0),
                         att_z=1.5, ignited=True, first_ignition_date=s[3], leader_score=0.9,
                         leader_rank=2, is_leader=True, sparkline=[1.0, float("nan"), 1.2]),
        ThemeMemberDaily(**member, ticker="AMD.US", tier="core", weight=0.25, leader_rank=1, is_leader=True),
        ThemeMemberDaily(**member, ticker="MRVL.US", tier="peripheral", weight=0.10, leader_rank=4),
        ThemeMemberDaily(**member, ticker="ARM.US", tier="peripheral", weight=0.20, leader_rank=None),
        ThemeMemberDaily(**member, ticker="ALAB.US", tier="peripheral", weight=0.20, leader_rank=None),
    ])
    interval = {"theme_id": theme.id, "tier": "core", "weight": 1.0}
    db.add_all([
        ThemeMember(**interval, ticker="NVDA.US", exposure=2, review_status="verified",
                    sources=["seed"], valid_from=date(2026, 1, 2), valid_to=s[0]),
        ThemeMember(**interval, ticker="NVDA.US", exposure=3, role="pure_play", review_status="verified",
                    sources=["seed", "etf:SMH.US"], evidence_quote="designs GPUs",
                    evidence_source="eodhd_description", evidence_as_of=s[0], valid_from=s[0]),
        # Closed by a later membership refresh exactly on the served as-of date.
        ThemeMember(**interval, ticker="AMD.US", exposure=2, role="supplier", review_status="etf_only",
                    sources=["etf:SMH.US"], valid_from=s[1], valid_to=s[5]),
        ThemeMember(theme_id=theme.id, ticker="MRVL.US", tier="peripheral", weight=0.4, exposure=1,
                    review_status="seed_unverified", sources=[{"channel": "keyword"}], valid_from=s[0]),
        ThemeMember(theme_id=theme.id, ticker="ARM.US", tier="peripheral", weight=0.4, exposure=1,
                    review_status="verified", sources=[], valid_from=s[0]),
        ThemeMember(theme_id=theme.id, ticker="ALAB.US", tier="peripheral", weight=0.4, exposure=1,
                    review_status="verified", sources=[], valid_from=s[0]),
    ])

    company = {"source_run_id": daily_5.id, "date": s[5]}
    db.add_all([
        CompanyTrendDaily(**company, ticker="BBB.US", rank=2, score=1.5, name="Bravo"),
        CompanyTrendDaily(**company, ticker="AAA.US", rank=1, score=2.5, name="Alpha", sector="Technology",
                          industry="Semiconductors", close=42.0, market_cap=1.2e10, r1=0.03,
                          x5=math.log(1.08), x5_sector=math.log(1.03), rvol5=math.log(2.5), att_z=None,
                          pth=0.99, theme_slugs=["ai-compute"], flags=["ignited"]),
        CompanyTrendDaily(**company, ticker="CCC.US", rank=3, score=0.5),
        CompanyTrendDaily(source_run_id=superseded_5.id, date=s[5], ticker="ZZZ.US", rank=1, score=9.0),
    ])
    await db.commit()
    return {"sessions": s, "theme": theme}


# ---------------------------------------------------------------------------
# Access control, availability and validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_routes_require_admin_key_unless_public(db_session, monkeypatch):
    from main import app

    with TestClient(app) as client:
        for path in (*THEME_GETS, "/api/personal/themes/proposals"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers={"X-API-Key": "wrong"}).status_code == 401
        decision = "/api/personal/themes/proposals/1/decision"
        assert client.post(decision, json={"decision": "accepted"}).status_code == 401

        monkeypatch.setattr(settings, "THEMES_PUBLIC", True)
        for path in THEME_GETS:
            # Open to everyone, but nothing is published yet.
            assert client.get(path).status_code in (404, 503)
        assert client.get("/api/v1/themes").status_code == 503
        # Proposal review stays admin-only even when theme reads are public.
        assert client.get("/api/personal/themes/proposals").status_code == 401
        assert client.post(decision, json={"decision": "accepted"}).status_code == 401

        monkeypatch.setattr(settings, "THEMES_PUBLIC", False)
        monkeypatch.setattr(settings, "ENVIRONMENT", "production")
        monkeypatch.setattr(settings, "ADMIN_API_KEY", "")
        disabled = client.get("/api/v1/themes")
        assert disabled.status_code == 503
        assert disabled.json()["detail"] == "Admin operations are disabled"


@pytest.mark.asyncio
async def test_theme_routes_return_503_until_published_and_404_for_unknown(db_session):
    await _add_theme(db_session, "ai-compute")
    await _add_theme(db_session, "old-theme", status="retired")
    # A publication row whose run failed is not a publication.
    failed = await _add_run(db_session, AS_OF, status="failed")
    _publish(db_session, failed, AS_OF)
    await db_session.commit()

    with pytest.raises(ThemeTrendsUnavailable):
        await get_theme_list(db_session)
    with pytest.raises(ThemeTrendsUnavailable):
        await get_trending_companies(db_session, as_of_date=AS_OF)
    with pytest.raises(ThemeNotFound):
        await get_theme_detail(db_session, "missing-theme")

    from main import app

    with TestClient(app) as client:
        for path in THEME_GETS:
            response = client.get(path, headers=ADMIN)
            assert response.status_code == 503, path
            assert "not been published" in response.json()["detail"]
        assert client.get("/api/v1/themes", params={"as_of_date": AS_OF.isoformat()},
                          headers=ADMIN).status_code == 503
        for path in ("/api/v1/themes/missing-theme", "/api/v1/themes/old-theme",
                     "/api/v1/themes/missing-theme/history", "/api/v1/themes/old-theme/history"):
            assert client.get(path, headers=ADMIN).status_code == 404, path


@pytest.mark.asyncio
async def test_theme_routes_validate_parameters(db_session):
    from main import app

    with TestClient(app) as client:
        invalid = (
            ("/api/v1/themes", {"view": "weekly"}),
            ("/api/v1/themes", {"stage": "hot"}),
            ("/api/v1/themes", {"as_of_date": "yesterday"}),
            ("/api/v1/themes/Bad_Slug", {}),
            ("/api/v1/themes/-leading-dash", {}),
            ("/api/v1/themes/ai-compute/history", {"days": 19}),
            ("/api/v1/themes/ai-compute/history", {"days": 505}),
            ("/api/v1/trending-companies", {"limit": 0}),
            ("/api/v1/trending-companies", {"limit": 201}),
            ("/api/personal/themes/proposals", {"status": "maybe"}),
        )
        for path, params in invalid:
            assert client.get(path, params=params, headers=ADMIN).status_code == 422, (path, params)
        rejected = client.post(
            "/api/personal/themes/proposals/1/decision",
            json={"decision": "maybe"},
            headers=ADMIN,
        )
        assert rejected.status_code == 422


# ---------------------------------------------------------------------------
# List: conversions, ordering, filters, metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_list_conversions_ordering_summary_and_filters(db_session, monkeypatch):
    monkeypatch.setattr(serving, "latest_completed_us_session", lambda reference: AS_OF)
    seeded = await _seed_list_publication(db_session)

    payload = await get_theme_list(db_session)
    validated = TypeAdapter(ThemeListResponse).validate_python(payload)
    assert validated.view == "now"
    assert [item.slug for item in validated.themes] == ["memory-storage", "ai-compute", "quantum", "tiny-theme"]
    assert validated.summary.model_dump() == {
        "ranked": 3, "emerging": 1, "accelerating": 0, "leading": 1, "crowded": 0, "fading": 1,
        "top_riser": "ai-compute",
    }

    compute = next(item for item in payload["themes"] if item["slug"] == "ai-compute")
    assert compute["name_en"] == "AI Compute"
    assert compute["stage"] == "leading" and compute["candidate_stage"] == "crowded"
    assert compute["stage_changed"] is True
    assert compute["rank_now"] == 2 and compute["rank_trend"] == 1 and compute["rank_change"] == 3
    assert compute["streak_top5"] == 4
    assert compute["excess_5d"] == pytest.approx(0.05)
    assert compute["excess_21d"] == pytest.approx(0.10)
    assert compute["excess_63d"] == pytest.approx(-0.10)
    assert compute["proxy_etf"] == "SMH.US"
    assert compute["proxy_excess_21d"] == pytest.approx(0.20)
    assert compute["pct_above_ma50"] == pytest.approx(0.7)
    assert compute["near_high_share"] == pytest.approx(0.4)
    assert compute["pct_above_ma50_change_10d"] == pytest.approx(0.12)
    assert compute["ignitions_today"] == 2 and compute["ignitions_5d"] == 5
    assert compute["n_members"] == 12 and compute["n_priced"] == 11
    assert compute["score_attention"] is None
    assert compute["attention_degraded"] is True
    assert compute["leaders"] == [{"ticker": "NVDA.US", "name": "NVIDIA"}, {"ticker": "AMD.US", "name": None}]
    # Published dates only: the superseded AS_OF row (heat_now 10) is ignored.
    assert compute["heat_sparkline"] == [60.0, 70.0, 80.0]

    memory = next(item for item in payload["themes"] if item["slug"] == "memory-storage")
    assert memory["emerging"] is True
    assert memory["leaders"] == [{"ticker": "MU.US", "name": "Micron"}]
    assert memory["heat_sparkline"] == [85.0]
    tiny = next(item for item in payload["themes"] if item["slug"] == "tiny-theme")
    assert tiny["rank_now"] is None and tiny["heat"] is None and tiny["excess_5d"] is None
    assert tiny["stage"] == "insufficient"

    trend = await get_theme_list(db_session, view="trend")
    assert [item["slug"] for item in trend["themes"]] == ["ai-compute", "memory-storage", "quantum", "tiny-theme"]

    fading = await get_theme_list(db_session, stage="fading")
    assert [item["slug"] for item in fading["themes"]] == ["quantum"]
    assert fading["themes"][0]["attention_only"] is True
    assert fading["summary"]["ranked"] == 3

    from main import app

    with TestClient(app) as client:
        response = client.get("/api/v1/themes", params={"view": "trend", "stage": "leading"}, headers=ADMIN)
        assert response.status_code == 200
        body = response.json()
        assert body["view"] == "trend"
        assert [item["slug"] for item in body["themes"]] == ["ai-compute"]
        assert body["meta"]["as_of_date"] == AS_OF.isoformat()
        assert body["meta"]["scoring_version"] == "theme-heat-v1"
        assert body["meta"]["taxonomy_version"] == "themes-v1"
        assert body["meta"]["stale"] is False

        exact = client.get("/api/v1/themes", params={"as_of_date": seeded["previous"][1].isoformat()},
                           headers=ADMIN)
        assert exact.status_code == 200
        assert exact.json()["meta"]["as_of_date"] == seeded["previous"][1].isoformat()
        assert [item["slug"] for item in exact.json()["themes"]] == ["ai-compute"]
        assert exact.json()["themes"][0]["heat_sparkline"] == [60.0, 70.0]

        missing = client.get("/api/v1/themes", params={"as_of_date": "2026-10-10"}, headers=ADMIN)
        assert missing.status_code == 404
        assert "2026-10-10" in missing.json()["detail"]
    with pytest.raises(ThemeTrendsDateUnavailable):
        await get_theme_list(db_session, as_of_date=date(2026, 10, 10))


@pytest.mark.asyncio
async def test_theme_meta_stale_flag_uses_new_york_six_am_cutoff(db_session, monkeypatch):
    await _seed_list_publication(db_session)

    monkeypatch.setattr(serving, "latest_completed_us_session", lambda reference: date(2026, 10, 12))
    payload = await get_theme_list(db_session)
    assert payload["meta"]["stale"] is True
    assert payload["meta"]["expected_as_of_date"] == "2026-10-12"

    monkeypatch.setattr(serving, "latest_completed_us_session", lambda reference: AS_OF)
    assert (await get_theme_list(db_session))["meta"]["stale"] is False

    # Real calendar. Tuesday 2026-10-13 05:30 ET (EDT = UTC-4): Monday's session is
    # not expected before 06:00 ET, so Friday's publication is still current.
    monkeypatch.undo()
    monkeypatch.setattr(serving, "utc_now", lambda: datetime(2026, 10, 13, 9, 30))
    early = await get_theme_list(db_session)
    assert early["meta"]["expected_as_of_date"] == AS_OF.isoformat()
    assert early["meta"]["stale"] is False

    monkeypatch.setattr(serving, "utc_now", lambda: datetime(2026, 10, 13, 10, 30))
    late = await get_theme_list(db_session)
    assert late["meta"]["expected_as_of_date"] == "2026-10-12"
    assert late["meta"]["stale"] is True

    from main import app

    with TestClient(app) as client:
        body = client.get("/api/v1/themes", headers=ADMIN).json()
    assert body["meta"]["stale"] is True


@pytest.mark.asyncio
async def test_theme_meta_quality_report_and_point_in_time_flag(db_session, monkeypatch):
    monkeypatch.setattr(serving, "latest_completed_us_session", lambda reference: AS_OF)
    # The pipeline's quality metrics: coverage_pct in percent next to the 0-1 fraction.
    seeded = await _seed_list_publication(db_session, quality_report={
        "passed": True,
        "metrics": {"attention_degraded": True, "coverage_pct": 97.37, "member_price_coverage": 0.9737},
        "errors": [],
        "warnings": ["attention degraded: 2 news days incomplete", "attention degraded: 2 news days incomplete"],
    })

    meta = (await get_theme_list(db_session))["meta"]
    assert meta["attention_degraded"] is True
    assert meta["coverage_pct"] == pytest.approx(97.37)
    assert meta["warnings"] == ["attention degraded: 2 news days incomplete"]
    assert meta["point_in_time"] is True

    # A report carrying only the fraction is still served in percent.
    run = await db_session.get(PipelineRun, seeded["run"].id)
    run.quality_report = {"metrics": {"member_price_coverage": 0.95}}
    await db_session.commit()
    assert (await get_theme_list(db_session))["meta"]["coverage_pct"] == pytest.approx(95.0)

    # The two earlier dates were backfilled with today's membership.
    backfilled = await get_theme_list(db_session, as_of_date=seeded["previous"][0])
    assert backfilled["meta"]["point_in_time"] is False
    assert backfilled["meta"]["attention_degraded"] is False
    assert backfilled["meta"]["coverage_pct"] is None
    assert backfilled["themes"][0]["point_in_time"] is False

    # A theme without members yet (scoring marks its rows pit=False) and a retired
    # theme's row do not make the publication "not point-in-time".
    empty = await _add_theme(db_session, "no-members-yet")
    db_session.add(_theme_day(
        seeded["run"], empty, AS_OF, pit=False, rankable=False, n_members=0, n_priced=0,
        stage="insufficient", candidate_stage="insufficient", heat=None, heat_now=None, heat_trend=None,
    ))
    retired_row = (await db_session.execute(
        select(ThemeDaily).where(
            ThemeDaily.source_run_id == seeded["run"].id,
            ThemeDaily.theme_id == seeded["themes"]["old-theme"].id,
        )
    )).scalar_one()
    retired_row.pit = False
    await db_session.commit()
    payload = await get_theme_list(db_session)
    assert payload["meta"]["point_in_time"] is True
    assert next(item for item in payload["themes"] if item["slug"] == "no-members-yet")["point_in_time"] is False

    row = (await db_session.execute(
        select(ThemeDaily).where(
            ThemeDaily.source_run_id == seeded["run"].id,
            ThemeDaily.theme_id == seeded["themes"]["quantum"].id,
        )
    )).scalar_one()
    row.pit = False
    await db_session.commit()
    meta = (await get_theme_list(db_session))["meta"]
    assert meta["point_in_time"] is False


@pytest.mark.asyncio
async def test_backfilled_dates_serve_their_own_quality_facts(db_session, monkeypatch):
    """A backfill run publishes many dates; meta must use each date's metrics.dates entry."""
    monkeypatch.setattr(serving, "latest_completed_us_session", lambda reference: AS_OF)
    seeded = await _seed_list_publication(db_session)
    earlier, later = seeded["previous"]
    backfill_run = (await db_session.execute(
        select(PipelineRun).join(DataPublication, DataPublication.pipeline_run_id == PipelineRun.id)
        .where(DataPublication.as_of_date == earlier)
    )).scalar_one()
    backfill_run.quality_report = {
        "passed": True,
        "warnings": ["target-only warning"],
        "metrics": {
            "as_of_date": later.isoformat(),
            "attention_degraded": False,
            "coverage_pct": 100.0,
            "dates": {
                earlier.isoformat(): {
                    "attention_degraded": True, "coverage_pct": 93.75,
                    "warnings": ["Attention degraded on this date"],
                },
                later.isoformat(): {"attention_degraded": False, "coverage_pct": 100.0, "warnings": []},
            },
        },
    }
    await db_session.commit()

    meta = (await get_theme_list(db_session, as_of_date=earlier))["meta"]
    assert meta["attention_degraded"] is True
    assert meta["coverage_pct"] == pytest.approx(93.75)
    assert meta["warnings"] == ["Attention degraded on this date"]

    meta = (await get_theme_list(db_session, as_of_date=later))["meta"]
    assert meta["attention_degraded"] is False
    assert meta["coverage_pct"] == pytest.approx(100.0)
    assert meta["warnings"] == []


@pytest.mark.asyncio
async def test_latest_publication_is_served_when_newer_run_failed(db_session, monkeypatch):
    monkeypatch.setattr(serving, "latest_completed_us_session", lambda reference: date(2026, 10, 12))
    seeded = await _seed_history_publication(db_session)
    s = seeded["sessions"]

    payload = await get_theme_list(db_session)
    # s[6] points at a failed run, so s[5] is served (flagged stale) instead of failing.
    assert payload["meta"]["as_of_date"] == s[5].isoformat()
    assert payload["meta"]["stale"] is True
    assert payload["themes"][0]["heat"] == pytest.approx(70.0)
    with pytest.raises(ThemeTrendsDateUnavailable):
        await get_theme_list(db_session, as_of_date=s[6])


async def _republish_as_of(themes, superseded_run_id: int, heat_now: float) -> int:
    """A same-date re-run committed by another session: new rows, repoint, sweep."""
    from database import async_session_maker

    async with async_session_maker() as writer:
        rerun = await _add_run(writer, AS_OF)
        writer.add_all([
            _theme_day(rerun, theme, AS_OF, heat_now=heat_now, rank_now=index + 1)
            for index, theme in enumerate(themes)
        ])
        await writer.execute(
            update(DataPublication)
            .where(DataPublication.dataset == THEME_TRENDS_DATASET, DataPublication.as_of_date == AS_OF)
            .values(pipeline_run_id=rerun.id)
        )
        await writer.execute(
            delete(ThemeDaily).where(ThemeDaily.source_run_id == superseded_run_id, ThemeDaily.date == AS_OF)
        )
        await writer.commit()
        return rerun.id


@pytest.mark.asyncio
async def test_theme_reads_reread_a_session_republished_mid_read(db_session, monkeypatch):
    monkeypatch.setattr(serving, "latest_completed_us_session", lambda reference: AS_OF)
    seeded = await _seed_list_publication(db_session)
    themes = [theme for theme in seeded["themes"].values() if theme.status != "retired"]
    original_context = serving._publication_context
    seen_runs: list[int] = []
    republish_with: list[float] = []

    async def racing_context(db, publication):
        # The re-run commits between selecting the publication and reading its rows.
        if republish_with:
            await _republish_as_of(themes, publication.pipeline_run_id, republish_with.pop())
        seen_runs.append(publication.pipeline_run_id)
        return await original_context(db, publication)

    monkeypatch.setattr(serving, "_publication_context", racing_context)

    republish_with.append(55.0)
    payload = await get_theme_list(db_session)
    first_run, rerun = seen_runs
    assert first_run == seeded["run"].id and rerun != first_run
    assert len(payload["themes"]) == 4
    assert {item["heat_now"] for item in payload["themes"]} == {55.0}
    assert payload["meta"]["point_in_time"] is True

    # Detail: the swept read finds no row for the theme, which is retried, not a 404.
    seen_runs.clear()
    republish_with.append(66.0)
    detail = await get_theme_detail(db_session, "ai-compute")
    assert len(seen_runs) == 2 and seen_runs[0] == rerun
    assert detail["theme"]["heat_now"] == pytest.approx(66.0)

    # A read that keeps losing the race gives up with 503 instead of mixing runs.
    monkeypatch.setattr(serving, "_publication_context", original_context)
    monkeypatch.setattr(serving, "_published_run_id", AsyncMock(return_value=None))
    with pytest.raises(ThemeTrendsUnavailable, match="republished"):
        await get_theme_list(db_session)
    with pytest.raises(ThemeTrendsUnavailable, match="republished"):
        await get_trending_companies(db_session)


# ---------------------------------------------------------------------------
# History and detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_history_aligns_published_rows_and_rebases_series(db_session, monkeypatch):
    monkeypatch.setattr(serving, "latest_completed_us_session", lambda reference: AS_OF)
    seeded = await _seed_history_publication(db_session)
    s = seeded["sessions"]

    payload = await get_theme_history(db_session, "ai-compute")
    TypeAdapter(ThemeHistoryResponse).validate_python(payload)
    history = payload["history"]
    assert payload["slug"] == "ai-compute"
    assert history["dates"] == [session.isoformat() for session in s[:6]]
    # Superseded (heat 1) and failed-run (heat 99) rows never appear.
    assert history["heat"] == [40.0, 41.0, 42.0, 43.0, 60.0, 70.0]
    assert history["heat_now"][:4] == [41.0, 42.0, 43.0, 44.0]
    assert history["stage"] == ["neutral"] * 4 + ["accelerating", "leading"]
    assert history["rank_now"] == [5, 4, 3, 2, 2, 1]
    assert history["ignitions"] == [0, 1, 2, 3, 4, 1]
    assert history["point_in_time"] == [False, False, True, True, True, True]
    assert history["pct_above_ma50"][:4] == [0.5] * 4
    # Same-run steps use the index ratio; run boundaries chain ret_1d (+10%, -50%).
    assert history["basket_index"] == pytest.approx([1.0, 1.1, 1.21, 1.331, 1.4641, 0.73205])
    assert history["benchmark_index"] == pytest.approx([1.0, 1.02, 1.04, 1.06, 1.08, 1.10])
    # The proxy has no bar on s0 and only a raw close on s3 (adjusted missing).
    assert history["proxy_index"][0] is None
    assert history["proxy_index"][1:] == pytest.approx([1.0, 1.1, 60 / 50, 1.3, 1.4])

    recent = (await get_theme_history(db_session, "ai-compute", days=3))["history"]
    assert recent["dates"] == [session.isoformat() for session in s[3:6]]
    assert recent["basket_index"] == pytest.approx([1.0, 1.1, 0.55])
    assert recent["benchmark_index"] == pytest.approx([1.0, 108 / 106, 110 / 106])

    from main import app

    with TestClient(app) as client:
        response = client.get("/api/v1/themes/ai-compute/history", params={"days": 20}, headers=ADMIN)
        assert response.status_code == 200
        body = response.json()
        assert body["meta"]["as_of_date"] == s[5].isoformat()
        assert len(body["history"]["dates"]) == 6
        assert all(
            len(values) == 6 for values in body["history"].values()
        )
        empty = client.get("/api/v1/themes/orphan-theme/history", headers=ADMIN)
        assert empty.status_code == 200
        assert empty.json()["history"]["dates"] == []
        assert empty.json()["meta"]["warnings"] == []


@pytest.mark.asyncio
async def test_theme_history_bridges_or_flags_unpublished_sessions(db_session, monkeypatch):
    monkeypatch.setattr(serving, "latest_completed_us_session", lambda reference: AS_OF)
    s = _sessions(AS_OF, 10)
    theme = await _add_theme(db_session, "ai-compute")
    backfill = await _add_run(db_session, s[3])
    for index, session in enumerate(s[:4]):
        _publish(db_session, backfill, session)
        db_session.add(_theme_day(backfill, theme, session, basket_index=1.1 ** index, ret_1d=None))
    # s4 and s7 were never published (their daily runs deferred); each daily run
    # restarts its own index base.
    daily = {}
    for index in (5, 6, 8, 9):
        daily[index] = await _add_run(db_session, s[index])
        _publish(db_session, daily[index], s[index])
    db_session.add_all([
        # Bridged from s0 with x5 (+20% vs SPY) and SPY's own s0 -> s5 return; the
        # 1-session ret_1d (+50%) covers s5 alone and must not stand in for s4.
        _theme_day(daily[5], theme, s[5], basket_index=3.0, ret_1d=0.5, x5=math.log(1.2)),
        _theme_day(daily[6], theme, s[6], basket_index=4.0, ret_1d=0.1),
        # No usable x_k: s8 is unmeasured and the basket is assumed to track SPY there.
        _theme_day(daily[8], theme, s[8], basket_index=5.0, ret_1d=0.2),
        _theme_day(daily[9], theme, s[9], basket_index=6.0, ret_1d=0.1),
    ])
    db_session.add(Ticker(ticker="SPY.US"))
    await db_session.flush()
    for index, session in enumerate(s):
        db_session.add(DailyPrice(ticker="SPY.US", date=session, adjusted_close=100 + 2 * index, close=100))
    await db_session.commit()

    payload = await get_theme_history(db_session, "ai-compute")
    TypeAdapter(ThemeHistoryResponse).validate_python(payload)
    history = payload["history"]
    assert history["dates"] == [s[index].isoformat() for index in (0, 1, 2, 3, 5, 6, 8, 9)]
    basket = history["basket_index"]
    assert basket[:6] == pytest.approx([1.0, 1.1, 1.21, 1.331, 1.2 * 1.10, 1.2 * 1.10 * 1.1])
    assert basket[6] is None
    assert basket[7] == pytest.approx(1.452 * (116 / 112) * 1.1)
    assert history["benchmark_index"] == pytest.approx([1.0, 1.02, 1.04, 1.06, 1.10, 1.12, 1.16, 1.18])
    assert len(payload["meta"]["warnings"]) == 1
    warning = payload["meta"]["warnings"][0]
    assert s[8].isoformat() in warning and s[5].isoformat() not in warning

    detail = await get_theme_detail(db_session, "ai-compute")
    assert detail["history"]["basket_index"][6] is None
    assert detail["meta"]["warnings"] == [warning]
    # The list view does not read history, so it carries no basket warning.
    assert (await get_theme_list(db_session))["meta"]["warnings"] == []


@pytest.mark.asyncio
async def test_theme_detail_members_brief_and_conversions(db_session, monkeypatch):
    monkeypatch.setattr(serving, "latest_completed_us_session", lambda reference: AS_OF)
    seeded = await _seed_history_publication(db_session)
    s = seeded["sessions"]

    payload = await get_theme_detail(db_session, "ai-compute")
    validated = TypeAdapter(ThemeDetailResponse).validate_python(payload)
    assert validated.meta.as_of_date == s[5]
    assert validated.theme.slug == "ai-compute"
    assert validated.theme.excess_5d == pytest.approx(0.05)
    assert validated.theme.heat_sparkline == [41.0, 42.0, 43.0, 44.0, 50.0, 50.0]
    assert [leader.model_dump() for leader in validated.theme.leaders] == [
        {"ticker": "AMD.US", "name": "Advanced Micro Devices"},
        {"ticker": "NVDA.US", "name": "NVIDIA Corporation"},
    ]
    assert validated.definition == "ai-compute definition"
    assert validated.keywords == ["alpha", "beta"]
    assert validated.proxy_etfs == ["SMH.US", "AIQ.US"]
    assert len(validated.history.dates) == 6
    # The stub brief agent raises NotImplementedError until it lands.
    assert validated.brief is None

    members = payload["members"]
    assert [member["ticker"] for member in members] == ["AMD.US", "NVDA.US", "MRVL.US", "ALAB.US", "ARM.US"]
    nvda = members[1]
    assert nvda["name"] == "NVIDIA Corporation"
    assert nvda["tier"] == "core" and nvda["weight"] == pytest.approx(0.30)
    assert nvda["exposure"] == 3 and nvda["role"] == "pure_play"
    assert nvda["review_status"] == "verified"
    assert nvda["sources"] == ["seed", "etf:SMH.US"]
    assert nvda["evidence_quote"] == "designs GPUs"
    assert nvda["evidence_source"] == "eodhd_description"
    assert nvda["evidence_as_of"] == s[0].isoformat()
    assert nvda["close"] == pytest.approx(120.5)
    assert nvda["return_1d"] == pytest.approx(0.02)
    assert nvda["excess_5d"] == pytest.approx(0.10)
    assert nvda["excess_21d"] == pytest.approx(0.25)
    assert nvda["near_high_ratio"] == pytest.approx(0.97)
    assert nvda["relative_volume_5d"] == pytest.approx(3.0)
    assert nvda["attention_z"] == pytest.approx(1.5)
    assert nvda["ignited"] is True
    assert nvda["first_ignition_date"] == s[3].isoformat()
    assert nvda["leader_rank"] == 2 and nvda["is_leader"] is True
    assert nvda["sparkline"] == [1.0, None, 1.2]
    amd = members[0]
    # Its interval was closed on the as-of date: the latest started interval is used.
    assert amd["exposure"] == 2 and amd["review_status"] == "etf_only"
    assert amd["name"] == "Advanced Micro Devices"
    assert amd["excess_5d"] is None and amd["relative_volume_5d"] is None
    mrvl = members[2]
    assert mrvl["sources"] == ["keyword"] and mrvl["name"] is None

    brief_calls = []

    async def fake_brief(db, theme_id, as_of):
        brief_calls.append((theme_id, as_of))
        return {
            "date": s[4].isoformat(),
            "model": "deepseek-chat",
            "prompt_version": "theme-brief-v1",
            "created_at": None,
            "content": "**盘面事实**\n- 篮子 X5 为 +5.0%。[E1]",
            "evidence": [{"id": "E1", "kind": "metric", "label": "X5 +5.0%"}],
        }

    monkeypatch.setattr("services.themes.briefs.latest_theme_brief", fake_brief)
    brief = (await get_theme_detail(db_session, "ai-compute"))["brief"]
    assert brief["stale"] is True
    assert brief_calls == [(seeded["theme"].id, s[5])]
    assert TypeAdapter(ThemeDetailResponse).validate_python(
        await get_theme_detail(db_session, "ai-compute")
    ).brief.evidence[0].id == "E1"

    async def fresh_brief(db, theme_id, as_of):
        return {**(await fake_brief(db, theme_id, as_of)), "date": s[5]}

    monkeypatch.setattr("services.themes.briefs.latest_theme_brief", fresh_brief)
    assert (await get_theme_detail(db_session, "ai-compute"))["brief"]["stale"] is False

    monkeypatch.setattr("services.themes.briefs.latest_theme_brief", AsyncMock(return_value=None))
    backfilled = await get_theme_detail(db_session, "ai-compute", as_of_date=s[3])
    assert backfilled["meta"]["as_of_date"] == s[3].isoformat()
    assert backfilled["meta"]["point_in_time"] is True
    assert backfilled["history"]["dates"] == [session.isoformat() for session in s[:4]]
    assert backfilled["members"] == []
    assert backfilled["theme"]["proxy_etf"] is None
    assert backfilled["brief"] is None

    with pytest.raises(ThemeNotFound):
        await get_theme_detail(db_session, "orphan-theme")
    with pytest.raises(ThemeNotFound):
        await get_theme_detail(db_session, "old-theme")

    from main import app

    with TestClient(app) as client:
        response = client.get("/api/v1/themes/ai-compute", headers=ADMIN)
        assert response.status_code == 200
        assert response.json()["members"][1]["sparkline"] == [1.0, None, 1.2]
        assert client.get("/api/v1/themes/orphan-theme", headers=ADMIN).status_code == 404
        assert client.get(
            "/api/v1/themes/ai-compute", params={"as_of_date": "2026-10-10"}, headers=ADMIN,
        ).status_code == 404


# ---------------------------------------------------------------------------
# Trending companies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trending_companies_conversions_and_limit(db_session, monkeypatch):
    monkeypatch.setattr(serving, "latest_completed_us_session", lambda reference: AS_OF)
    seeded = await _seed_history_publication(db_session)
    s = seeded["sessions"]

    payload = await get_trending_companies(db_session)
    TypeAdapter(TrendingCompaniesResponse).validate_python(payload)
    assert [company["ticker"] for company in payload["companies"]] == ["AAA.US", "BBB.US", "CCC.US"]
    top = payload["companies"][0]
    assert top["rank"] == 1 and top["score"] == pytest.approx(2.5)
    assert top["name"] == "Alpha" and top["sector"] == "Technology" and top["industry"] == "Semiconductors"
    assert top["close"] == pytest.approx(42.0) and top["market_cap"] == pytest.approx(1.2e10)
    assert top["return_1d"] == pytest.approx(0.03)
    assert top["excess_5d"] == pytest.approx(0.08)
    assert top["excess_5d_vs_sector"] == pytest.approx(0.03)
    assert top["relative_volume_5d"] == pytest.approx(2.5)
    assert top["attention_z"] is None
    assert top["near_high_ratio"] == pytest.approx(0.99)
    assert top["themes"] == ["ai-compute"] and top["flags"] == ["ignited"]
    assert payload["companies"][2]["excess_5d"] is None

    backfilled = await get_trending_companies(db_session, as_of_date=s[3])
    assert backfilled["companies"] == []

    from main import app

    with TestClient(app) as client:
        response = client.get("/api/v1/trending-companies", params={"limit": 2}, headers=ADMIN)
        assert response.status_code == 200
        assert [company["rank"] for company in response.json()["companies"]] == [1, 2]
        assert client.get(
            "/api/v1/trending-companies", params={"as_of_date": "2026-10-10"}, headers=ADMIN,
        ).status_code == 404


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------


async def _seed_proposals(db) -> dict:
    proposals = {
        "old": ThemeProposal(proposal_key="k-old", label_en="Space Launch", label_zh="商业航天",
                             slug_suggestion="Space Launch!", rationale="同期新闻包括发射合同。[E1]",
                             tickers=["RKLB.US", "BRK-B.US"], evidence=[{"id": "E1", "title": "Launch"}],
                             first_seen=date(2026, 9, 1), last_seen=date(2026, 9, 26), hit_count=2),
        "new": ThemeProposal(proposal_key="k-new", label_en="Drone Defense", label_zh="无人机",
                             slug_suggestion="drone-defense", rationale="Counter-drone systems.",
                             tickers=["AVAV.US"], evidence=None,
                             first_seen=date(2026, 10, 3), last_seen=date(2026, 10, 3), hit_count=1),
        "rejected": ThemeProposal(proposal_key="k-rej", label_en="Meme", tickers=["GME.US"],
                                  first_seen=date(2026, 10, 3), last_seen=date(2026, 10, 10), hit_count=1,
                                  status="rejected"),
    }
    db.add_all(proposals.values())
    await db.commit()
    return proposals


@pytest.mark.asyncio
async def test_theme_proposals_list_order_and_decisions(db_session):
    proposals = await _seed_proposals(db_session)
    ids = {key: proposal.id for key, proposal in proposals.items()}

    listed = await list_theme_proposals(db_session)
    TypeAdapter(ThemeProposalsResponse).validate_python(listed)
    assert [item["id"] for item in listed["proposals"]] == [
        proposals["new"].id, proposals["old"].id, proposals["rejected"].id,
    ]
    pending = await list_theme_proposals(db_session, status="pending")
    assert [item["id"] for item in pending["proposals"]] == [proposals["new"].id, proposals["old"].id]
    assert pending["proposals"][1]["evidence"] == [{"id": "E1", "title": "Launch"}]
    assert pending["proposals"][0]["evidence"] == []

    result = await decide_theme_proposal(db_session, proposals["old"].id, "accepted")
    TypeAdapter(ThemeProposalDecisionResponse).validate_python(result)
    assert result["proposal"]["status"] == "accepted"
    snippet = result["yaml_snippet"]
    assert snippet.startswith("  - slug: space-launch\n")
    taxonomy = parse_taxonomy("version: test\nthemes:\n" + snippet)
    definition = taxonomy.themes[0]
    assert definition.slug == "space-launch"
    assert definition.name_en == "Space Launch" and definition.name_zh == "商业航天"
    assert definition.definition == "同期新闻包括发射合同。[E1]"
    assert definition.seed_core == ["RKLB.US", "BRK-B.US"]
    assert definition.status == "active"
    db_session.expire_all()
    stored = await db_session.get(ThemeProposal, ids["old"])
    assert stored.status == "accepted" and stored.decided_at is not None

    with pytest.raises(ThemeProposalNotFound):
        await decide_theme_proposal(db_session, 9999, "accepted")

    from main import app

    with TestClient(app) as client:
        response = client.post(
            f"/api/personal/themes/proposals/{ids['new']}/decision",
            json={"decision": "rejected"},
            headers=ADMIN,
        )
        assert response.status_code == 200
        assert response.json()["proposal"]["status"] == "rejected"
        assert response.json()["yaml_snippet"] is None
        missing = client.post(
            "/api/personal/themes/proposals/9999/decision",
            json={"decision": "accepted"},
            headers=ADMIN,
        )
        assert missing.status_code == 404
        listed = client.get("/api/personal/themes/proposals", params={"status": "rejected"}, headers=ADMIN)
        assert listed.status_code == 200
        assert {item["id"] for item in listed.json()["proposals"]} == {ids["new"], ids["rejected"]}


@pytest.mark.asyncio
async def test_accepted_proposal_slug_avoids_existing_and_file_slugs(db_session, monkeypatch):
    taxonomy_text = (
        "version: test\nthemes:\n"
        "  - slug: file-only\n    name_en: File only\n    name_zh: 文件\n"
        "    definition: Listed in the file but not synced yet.\n"
    )
    monkeypatch.setattr(serving, "load_taxonomy", lambda: parse_taxonomy(taxonomy_text))
    long_slug = "a" * 64
    await _add_theme(db_session, "ai-compute")
    # Retired themes keep their rows; reusing the slug would silently reactivate one.
    await _add_theme(db_session, "ai-compute-2", status="retired")
    await _add_theme(db_session, long_slug)
    proposals = {
        key: ThemeProposal(proposal_key=f"k-{key}", label_en=label, slug_suggestion=slug, rationale="Why.",
                           tickers=["NVDA.US"], first_seen=date(2026, 10, 1), last_seen=date(2026, 10, 1))
        for key, label, slug in (
            ("db", "AI Compute", "ai-compute"),
            ("file", "File Only", "file-only"),
            ("long", "Long", long_slug),
            ("broken", "AI Compute again", "ai-compute"),
        )
    }
    db_session.add_all(proposals.values())
    await db_session.commit()

    expected = {"db": "ai-compute-3", "file": "file-only-2", "long": "a" * 62 + "-2"}
    for key, slug in expected.items():
        snippet = (await decide_theme_proposal(db_session, proposals[key].id, "accepted"))["yaml_snippet"]
        assert snippet.startswith(f"  - slug: {slug}\n"), key
        # Pasting the entry under the existing file keeps the taxonomy valid.
        assert parse_taxonomy(taxonomy_text + snippet).themes[-1].slug == slug

    def broken_taxonomy():
        raise ValueError("duplicate theme slugs: ['x']")

    # An unreadable taxonomy file still leaves the DB slugs guarded.
    monkeypatch.setattr(serving, "load_taxonomy", broken_taxonomy)
    snippet = (await decide_theme_proposal(db_session, proposals["broken"].id, "accepted"))["yaml_snippet"]
    assert snippet.startswith("  - slug: ai-compute-3\n")


# ---------------------------------------------------------------------------
# Page views never call providers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_reads_make_no_external_calls(db_session, monkeypatch):
    monkeypatch.setattr(serving, "latest_completed_us_session", lambda reference: AS_OF)
    await _seed_history_publication(db_session)
    await _seed_proposals(db_session)

    async def forbidden_send(self, *args, **kwargs):
        raise AssertionError("theme reads must not make HTTP calls")

    fetch = AsyncMock(side_effect=AssertionError("EODHD must not be called"))
    create_client = Mock(side_effect=AssertionError("DeepSeek must not be called"))
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden_send)
    monkeypatch.setattr(eodhd_client, "_fetch_from_eodhd", fetch)
    monkeypatch.setattr(deepseek_client, "_create_client", create_client)

    from main import app

    with TestClient(app) as client:
        for path, params in (
            ("/api/v1/themes", {}),
            ("/api/v1/themes", {"view": "trend"}),
            ("/api/v1/themes/ai-compute", {}),
            ("/api/v1/themes/ai-compute/history", {"days": 504}),
            ("/api/v1/trending-companies", {"limit": 200}),
            ("/api/personal/themes/proposals", {}),
        ):
            response = client.get(path, params=params, headers=ADMIN)
            assert response.status_code == 200, (path, response.text)
    fetch.assert_not_awaited()
    create_client.assert_not_called()
