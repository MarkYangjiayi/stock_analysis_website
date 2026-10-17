"""theme_trends publication pipeline (spec §3.4) against the real scoring module."""

import math
from datetime import date, timedelta

import numpy as np
import pytest
from sqlalchemy import delete, func, insert, select

from core.config import settings
from models import (
    CompanyTrendDaily,
    DailyPrice,
    DataPublication,
    NewsIngestDay,
    PipelineRun,
    StockScreenerSnapshot,
    Theme,
    ThemeDaily,
    ThemeMember,
    ThemeMemberDaily,
    Ticker,
    TickerAttentionDaily,
    UniverseMembership,
)
from services.data_quality import DataQualityError
from services.market_breadth import market_sessions_through
from services.themes import pipeline, serving
from services.themes.membership import THEME_MEMBERSHIP_DATASET
from services.themes.news_ingest import NEWS_ATTENTION_DATASET
from services.themes.pipeline import (
    THEME_TRENDS_DATASET,
    build_price_frames,
    evaluate_theme_trends,
    refresh_theme_trends,
    theme_trends_rerun_due,
)
from services.themes.scoring import SCORING_VERSION
from services.themes.types import ScoringResult
from services.universe import LIVE_UNIVERSE_SOURCE


TARGET = date(2026, 10, 9)  # Friday
PRICE_SESSIONS = market_sessions_through(TARGET, 300)
MEMBERSHIP_FROM = PRICE_SESSIONS[-120]
AI = [f"AIC{index}.US" for index in range(1, 9)]
GRID = [f"GRD{index}.US" for index in range(1, 9)]
BANKS = [f"BNK{index}.US" for index in range(1, 9)]
UNIVERSE = AI + GRID + BANKS
SPY = "SPY.US"
SMH = "SMH.US"
CLASSIFICATION = {
    **{ticker: ("Technology", "Semiconductors") for ticker in AI},
    **{ticker: ("Utilities", "Utilities - Regulated Electric") for ticker in GRID},
    **{ticker: ("Financial Services", "Banks - Regional") for ticker in BANKS},
}
THEMES = {
    "ai-compute": {"members": AI, "proxy_etfs": [SMH]},
    "grid-power": {"members": GRID, "proxy_etfs": ["GRID.US"]},  # proxy never priced
}


def _synthetic_prices() -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    rng = np.random.default_rng(20261009)
    n = len(PRICE_SESSIONS)
    market = rng.normal(0.0003, 0.008, n)
    ai_factor = rng.normal(0.0, 0.008, n)
    ai_factor[-40:] += 0.006  # a late, broad run-up: the hottest theme
    grid_factor = rng.normal(0.0, 0.005, n)
    grid_factor[-40:] -= 0.002
    closes = {SPY: 450.0 * np.cumprod(1 + market), SMH: 200.0 * np.cumprod(1 + market + ai_factor)}
    for index, ticker in enumerate(AI):
        closes[ticker] = (40.0 + 5 * index) * np.cumprod(1 + market + ai_factor + rng.normal(0, 0.012, n))
    for index, ticker in enumerate(GRID):
        closes[ticker] = (30.0 + 3 * index) * np.cumprod(1 + market + grid_factor + rng.normal(0, 0.009, n))
    for index, ticker in enumerate(BANKS):
        closes[ticker] = (25.0 + 2 * index) * np.cumprod(1 + market + rng.normal(0, 0.010, n))
    volumes = {ticker: rng.lognormal(math.log(2_000_000), 0.2, n) for ticker in closes}
    for ticker in AI:
        volumes[ticker][-5:] *= 2.5
    return closes, volumes


async def _publish(db, dataset: str, as_of: date, metrics: dict | None = None) -> int:
    run = PipelineRun(
        pipeline_name=f"{dataset}_fixture",
        target_date=as_of,
        status="published",
        stage="published",
        quality_report={"passed": True, "metrics": metrics or {}, "errors": [], "warnings": []},
    )
    db.add(run)
    await db.flush()
    db.add(DataPublication(dataset=dataset, as_of_date=as_of, pipeline_run_id=run.id, status="published"))
    await db.flush()
    return run.id


async def _publish_screener_day(db, day: date) -> None:
    for ticker in UNIVERSE:
        sector, industry = CLASSIFICATION[ticker]
        db.add(StockScreenerSnapshot(
            ticker=ticker, date=day, name=f"{ticker[:-3]} Corp", exchange="NASDAQ",
            sector=sector, industry=industry, market_cap=10_000_000_000,
        ))
    await _publish(db, "screener", day)
    await _publish(db, "price_history", day)


async def seed_world(
    db,
    *,
    screener_days=(TARGET,),
    membership_from: date = MEMBERSHIP_FROM,
    news_published: bool = True,
    incomplete_days=(),
    memberless=(),
) -> dict[str, int]:
    closes, volumes = _synthetic_prices()
    await db.execute(insert(Ticker), [{"ticker": ticker} for ticker in closes])
    await db.execute(insert(DailyPrice), [
        {
            "ticker": ticker,
            "date": session,
            "open": float(closes[ticker][index]),
            "high": float(closes[ticker][index]) * 1.01,
            "low": float(closes[ticker][index]) * 0.99,
            "close": float(closes[ticker][index]),
            "adjusted_close": float(closes[ticker][index]),
            "volume": int(volumes[ticker][index]),
        }
        for ticker in closes
        for index, session in enumerate(PRICE_SESSIONS)
    ])
    for ticker in UNIVERSE:
        db.add(UniverseMembership(
            universe="RUSSELL3000", ticker=ticker, effective_from=date(2020, 1, 1), source=LIVE_UNIVERSE_SOURCE,
        ))
    theme_ids = {}
    for slug, spec in THEMES.items():
        theme = Theme(
            slug=slug, name_en=slug.replace("-", " ").title(), name_zh=slug, definition=f"{slug} definition",
            keywords=[], negative_keywords=[], proxy_etfs=spec["proxy_etfs"], seed_core=[], pins=[], bans=[],
            status="active", taxonomy_version="themes-test-v1",
        )
        db.add(theme)
        await db.flush()
        theme_ids[slug] = theme.id
        for index, ticker in enumerate(spec["members"]):
            core = index < 6
            db.add(ThemeMember(
                theme_id=theme.id, ticker=ticker, tier="core" if core else "peripheral",
                weight=1.0 if core else 0.4, exposure=3 if core else 1, role="pure_play", sources=["seed"],
                review_status="verified", valid_from=membership_from,
            ))
    for slug in memberless:  # e.g. added to the taxonomy since the last membership refresh
        theme = Theme(
            slug=slug, name_en=slug.title(), name_zh=slug, definition=f"{slug} definition", keywords=[],
            negative_keywords=[], proxy_etfs=[], seed_core=[], pins=[], bans=[], status="active",
            taxonomy_version="themes-test-v1",
        )
        db.add(theme)
        await db.flush()
        theme_ids[slug] = theme.id
    retired = Theme(
        slug="retired-theme", name_en="Retired", name_zh="退役", definition="gone", keywords=[],
        negative_keywords=[], proxy_etfs=[], seed_core=[], pins=[], bans=[], status="retired",
        taxonomy_version="themes-test-v1",
    )
    db.add(retired)
    await db.flush()
    db.add(ThemeMember(
        theme_id=retired.id, ticker=BANKS[0], tier="core", weight=1.0, exposure=3, sources=["seed"],
        review_status="verified", valid_from=membership_from,
    ))
    await _publish(db, THEME_MEMBERSHIP_DATASET, membership_from)

    rng = np.random.default_rng(7)
    first_day = PRICE_SESSIONS[0] - timedelta(days=120)
    days = [first_day + timedelta(days=offset) for offset in range((TARGET - first_day).days + 1)]
    await db.execute(insert(NewsIngestDay), [
        {
            "day": day, "status": "partial" if day in incomplete_days else "complete", "pages": 2,
            "articles_total": 1500, "articles_us": 1000, "truncated": day in incomplete_days,
        }
        for day in days
    ])
    attention = []
    for ticker in UNIVERSE:
        counts = rng.poisson(1.0, len(days))
        if ticker in AI:
            counts[-7:] += 6  # the hot theme is also in the news
        attention.extend(
            {"ticker": ticker, "day": day, "article_count": int(count), "focused_count": int(count)}
            for day, count in zip(days, counts)
            if count > 0
        )
    await db.execute(insert(TickerAttentionDaily), attention)
    for day in screener_days:
        await _publish_screener_day(db, day)
    if news_published:
        for day in screener_days:
            await _publish(db, NEWS_ATTENTION_DATASET, day)
    await db.commit()
    return {**theme_ids, "retired-theme": retired.id}


async def _publication_run(db, day: date):
    return await db.scalar(select(DataPublication.pipeline_run_id).where(
        DataPublication.dataset == THEME_TRENDS_DATASET, DataPublication.as_of_date == day,
    ))


async def _count(db, model, **filters):
    stmt = select(func.count()).select_from(model)
    for key, value in filters.items():
        stmt = stmt.where(getattr(model, key) == value)
    return await db.scalar(stmt)


@pytest.mark.asyncio
async def test_end_to_end_publishes_theme_member_and_company_rows(db_session):
    theme_ids = await seed_world(db_session)

    result = await refresh_theme_trends(TARGET)

    assert result["status"] == "published"
    assert result["as_of_date"] == TARGET.isoformat()
    assert result["rankable"] == 2
    assert result["published_dates"] == 1
    run_id = result["run_id"]
    assert await _publication_run(db_session, TARGET) == run_id

    run = await db_session.get(PipelineRun, run_id)
    assert (run.pipeline_name, run.status, run.version) == ("theme_trends", "published", SCORING_VERSION)
    report = run.quality_report
    assert report["passed"] is True and report["errors"] == []
    metrics = report["metrics"]
    assert metrics["coverage_pct"] == 100.0
    assert metrics["active_member_tickers"] == 16
    assert metrics["attention_degraded"] is False
    assert metrics["news_attention_published"] is True
    assert metrics["taxonomy_version"] == "themes-test-v1"
    assert metrics["membership_as_of"] == MEMBERSHIP_FROM.isoformat()
    assert metrics["sessions"] == pipeline.SCORING_SESSIONS
    assert run.records_processed == result["theme_rows"] + result["member_rows"] + result["company_rows"]

    rows = (await db_session.execute(select(ThemeDaily).where(ThemeDaily.source_run_id == run_id))).scalars().all()
    by_theme = {row.theme_id: row for row in rows}
    # Only non-retired themes are scored; ids are mapped by slug.
    assert set(by_theme) == {theme_ids["ai-compute"], theme_ids["grid-power"]}
    ai = by_theme[theme_ids["ai-compute"]]
    grid = by_theme[theme_ids["grid-power"]]
    for row in (ai, grid):
        assert row.date == TARGET and row.scoring_version == SCORING_VERSION
        assert row.pit is True and row.rankable is True and row.n_priced == 8
        assert row.heat is not None and math.isfinite(row.heat_now) and math.isfinite(row.heat_trend)
        assert row.attention_degraded is False and row.score_attention is not None
        assert row.stage in {"emerging", "accelerating", "leading", "crowded", "fading", "neutral"}
        assert len(row.leaders) == 3
    assert {ai.rank_now, grid.rank_now} == {1, 2}
    assert ai.rank_now == 1 and ai.x21 > grid.x21
    assert ai.proxy_etf == SMH and ai.proxy_x21 is not None
    assert grid.proxy_etf is None and grid.proxy_x21 is None

    members = (await db_session.execute(
        select(ThemeMemberDaily).where(ThemeMemberDaily.source_run_id == run_id)
    )).scalars().all()
    assert len(members) == 16 and {member.date for member in members} == {TARGET}
    ai_members = [member for member in members if member.theme_id == theme_ids["ai-compute"]]
    assert {member.ticker for member in ai_members} == set(AI)
    assert sum(member.is_leader for member in ai_members) == 3
    assert {member.tier for member in ai_members} == {"core", "peripheral"}
    assert all(member.close is not None and len(member.sparkline) == 60 for member in members)

    companies = (await db_session.execute(
        select(CompanyTrendDaily).where(CompanyTrendDaily.source_run_id == run_id).order_by(CompanyTrendDaily.rank)
    )).scalars().all()
    assert companies and [company.rank for company in companies] == list(range(1, len(companies) + 1))
    assert {company.ticker for company in companies} <= set(UNIVERSE)
    assert all(company.date == TARGET for company in companies)
    ai_company = next(company for company in companies if company.ticker in AI)
    assert ai_company.theme_slugs == ["ai-compute"]

    # The serving layer reads this publication as the current one.
    payload = await serving.get_theme_list(db_session, as_of_date=TARGET)
    assert payload["meta"]["as_of_date"] == TARGET.isoformat()
    assert payload["meta"]["point_in_time"] is True
    assert payload["meta"]["attention_degraded"] is False
    assert payload["meta"]["coverage_pct"] == 100.0


@pytest.mark.asyncio
async def test_deferred_on_missing_dependencies_creates_no_run(db_session):
    result = await refresh_theme_trends(TARGET)
    assert result == {
        "status": "deferred",
        "reason": "missing-publications",
        "missing": ["screener", "price_history", THEME_MEMBERSHIP_DATASET],
        "as_of_date": TARGET.isoformat(),
    }
    # Screener and prices for the target exist, but membership is only published later.
    await _publish_screener_day(db_session, TARGET)
    await _publish(db_session, THEME_MEMBERSHIP_DATASET, TARGET + timedelta(days=3))
    await db_session.commit()
    result = await refresh_theme_trends(TARGET)
    assert result["status"] == "deferred" and result["missing"] == [THEME_MEMBERSHIP_DATASET]
    assert await _count(db_session, PipelineRun, pipeline_name="theme_trends") == 0


@pytest.mark.asyncio
async def test_non_session_target_normalizes_to_previous_session(db_session):
    await seed_world(db_session)
    result = await refresh_theme_trends(date(2026, 10, 11))  # Sunday
    assert result["status"] == "published" and result["as_of_date"] == TARGET.isoformat()


@pytest.mark.asyncio
async def test_skip_when_already_published(db_session):
    await seed_world(db_session)
    first = await refresh_theme_trends(TARGET)
    second = await refresh_theme_trends(TARGET)
    assert second == {
        "status": "skipped",
        "reason": "already-published",
        "as_of_date": TARGET.isoformat(),
        "run_id": first["run_id"],
    }
    assert await _count(db_session, PipelineRun, pipeline_name="theme_trends") == 1
    assert await theme_trends_rerun_due(TARGET) is False


@pytest.mark.asyncio
async def test_gate_failure_publishes_nothing_and_keeps_previous_publication(db_session, monkeypatch):
    previous = PRICE_SESSIONS[-2]
    await seed_world(db_session, screener_days=(previous, TARGET))
    first = await refresh_theme_trends(previous)
    assert first["status"] == "published"
    rows_before = await _count(db_session, ThemeDaily)

    monkeypatch.setattr(settings, "PIPELINE_MIN_RANKABLE_THEMES", 3)
    with pytest.raises(DataQualityError, match="rankable themes"):
        await refresh_theme_trends(TARGET)

    assert await _publication_run(db_session, TARGET) is None
    assert await _publication_run(db_session, previous) == first["run_id"]
    assert await _count(db_session, ThemeDaily) == rows_before
    assert await _count(db_session, ThemeMemberDaily, date=TARGET) == 0
    assert await _count(db_session, CompanyTrendDaily, date=TARGET) == 0
    failed = (await db_session.execute(
        select(PipelineRun).where(PipelineRun.pipeline_name == "theme_trends", PipelineRun.status == "failed")
    )).scalars().one()
    assert failed.target_date == TARGET
    assert failed.quality_report["passed"] is False
    assert failed.quality_report["metrics"]["rankable_themes"] == 2
    assert "Data quality gate failed" in failed.error_message

    # Readers keep serving the previous publication.
    payload = await serving.get_theme_list(db_session)
    assert payload["meta"]["as_of_date"] == previous.isoformat()


@pytest.mark.asyncio
async def test_gate_fails_when_member_prices_are_missing_on_target(db_session):
    await seed_world(db_session)
    await db_session.execute(delete(DailyPrice).where(DailyPrice.ticker.in_(AI[:3]), DailyPrice.date == TARGET))
    await db_session.commit()

    with pytest.raises(DataQualityError) as raised:
        await refresh_theme_trends(TARGET)

    errors = raised.value.report.errors
    assert any("price coverage 81.2%" in error and "AIC1.US" in error for error in errors)
    assert await _publication_run(db_session, TARGET) is None


@pytest.mark.asyncio
async def test_attention_degraded_run_is_rerun_once_after_news_publishes(db_session):
    late_days = (TARGET, TARGET - timedelta(days=1))
    await seed_world(db_session, news_published=False, incomplete_days=late_days)

    first = await refresh_theme_trends(TARGET)
    assert first["status"] == "published" and first["attention_degraded"] is True
    run = await db_session.get(PipelineRun, first["run_id"])
    assert run.quality_report["metrics"]["attention_degraded"] is True
    assert run.quality_report["metrics"]["news_attention_published"] is False
    assert any("Attention degraded" in warning for warning in run.quality_report["warnings"])
    degraded = (await db_session.execute(select(ThemeDaily))).scalars().all()
    assert all(row.attention_degraded and row.score_attention is None for row in degraded)
    assert all("incomplete_news_days" in row.flags["attention_degraded_reasons"] for row in degraded)
    # Degraded but nothing new to read yet: still skipped.
    assert (await refresh_theme_trends(TARGET))["status"] == "skipped"

    await db_session.execute(
        NewsIngestDay.__table__.update().where(NewsIngestDay.day.in_(late_days)).values(status="complete")
    )
    await _publish(db_session, NEWS_ATTENTION_DATASET, TARGET)
    await db_session.commit()
    assert await theme_trends_rerun_due(TARGET) is True

    second = await refresh_theme_trends(TARGET)
    assert second["status"] == "published" and second["run_id"] != first["run_id"]
    assert second["rerun_reason"] == "news-attention-published"
    assert second["attention_degraded"] is False
    assert await _publication_run(db_session, TARGET) == second["run_id"]
    # The superseded run's rows for the date are gone.
    assert await _count(db_session, ThemeDaily, source_run_id=first["run_id"]) == 0
    assert await _count(db_session, ThemeMemberDaily, source_run_id=first["run_id"]) == 0
    assert await _count(db_session, CompanyTrendDaily, source_run_id=first["run_id"]) == 0

    assert await theme_trends_rerun_due(TARGET) is False
    assert (await refresh_theme_trends(TARGET))["status"] == "skipped"


@pytest.mark.asyncio
async def test_backfill_publishes_each_date_under_one_run_with_pit_flags(db_session):
    membership_from = PRICE_SESSIONS[-3]
    await seed_world(db_session, membership_from=membership_from)

    result = await refresh_theme_trends(TARGET, backfill_sessions=5)

    assert result["status"] == "published" and result["published_dates"] == 5
    dates = PRICE_SESSIONS[-5:]
    assert result["first_published_date"] == dates[0].isoformat()
    publications = (await db_session.execute(
        select(DataPublication.as_of_date, DataPublication.pipeline_run_id)
        .where(DataPublication.dataset == THEME_TRENDS_DATASET)
        .order_by(DataPublication.as_of_date)
    )).all()
    assert publications == [(day, result["run_id"]) for day in dates]
    assert await _count(db_session, PipelineRun, pipeline_name="theme_trends") == 1

    rows = (await db_session.execute(select(ThemeDaily).order_by(ThemeDaily.date))).scalars().all()
    assert len(rows) == 10
    pit_by_date = {}
    for row in rows:
        pit_by_date.setdefault(row.date, set()).add(row.pit)
    # Dates before the first membership interval borrow today's members (not point-in-time).
    assert pit_by_date == {day: {day >= membership_from} for day in dates}
    assert {row.date for row in (await db_session.execute(select(ThemeMemberDaily))).scalars()} == {TARGET}
    assert {row.date for row in (await db_session.execute(select(CompanyTrendDaily))).scalars()} == {TARGET}

    run = await db_session.get(PipelineRun, result["run_id"])
    assert run.quality_report["metrics"]["sessions"] == pipeline.SCORING_SESSIONS + 5
    assert run.quality_report["metrics"]["backfill_sessions"] == 5

    early = await serving.get_theme_list(db_session, as_of_date=dates[0])
    assert early["meta"]["point_in_time"] is False
    latest = await serving.get_theme_list(db_session)
    assert latest["meta"]["as_of_date"] == TARGET.isoformat() and latest["meta"]["point_in_time"] is True


@pytest.mark.asyncio
async def test_same_date_rerun_sweeps_only_superseded_rows(db_session):
    older, middle = PRICE_SESSIONS[-3], PRICE_SESSIONS[-2]
    await seed_world(db_session, screener_days=(older, TARGET))
    run_a = (await refresh_theme_trends(older))["run_id"]
    run_b_result = await refresh_theme_trends(TARGET)
    run_b = run_b_result["run_id"]
    b_rows = run_b_result["theme_rows"] + run_b_result["member_rows"] + run_b_result["company_rows"]

    # backfill_sessions > 0 bypasses the skip: republish middle and TARGET under run C.
    run_c_result = await refresh_theme_trends(TARGET, backfill_sessions=2)
    run_c = run_c_result["run_id"]

    assert run_c_result["swept_rows"] == b_rows
    assert await _publication_run(db_session, older) == run_a
    assert await _publication_run(db_session, middle) == run_c
    assert await _publication_run(db_session, TARGET) == run_c
    for model in (ThemeDaily, ThemeMemberDaily, CompanyTrendDaily):
        assert await _count(db_session, model, source_run_id=run_b) == 0
        assert await _count(db_session, model, source_run_id=run_a) > 0
    assert await _count(db_session, ThemeDaily, source_run_id=run_c) == 4
    assert await _count(db_session, ThemeDaily, source_run_id=run_a, date=older) == 2


@pytest.mark.asyncio
async def test_unrankable_theme_does_not_flag_the_run_attention_degraded(db_session):
    # News is complete but news_attention is not published yet (the 04:50 case), and a
    # theme added to the taxonomy has no members until the weekly membership refresh.
    await seed_world(db_session, news_published=False, memberless=("new-theme",))

    result = await refresh_theme_trends(TARGET)

    assert result["status"] == "published" and result["attention_degraded"] is False
    rows = {
        theme.slug: row
        for row, theme in (await db_session.execute(
            select(ThemeDaily, Theme).join(Theme, Theme.id == ThemeDaily.theme_id)
        )).all()
    }
    assert rows["new-theme"].rankable is False
    assert rows["ai-compute"].rankable and rows["grid-power"].rankable
    run = await db_session.get(PipelineRun, result["run_id"])
    metrics = run.quality_report["metrics"]
    assert metrics["attention_degraded"] is False and metrics["attention_degraded_themes"] == []
    assert not any("Attention degraded" in warning for warning in run.quality_report["warnings"])
    assert metrics["dates"][TARGET.isoformat()]["attention_degraded"] is False

    # Publishing news_attention later must not trigger the 07:50 re-run.
    await _publish(db_session, NEWS_ATTENTION_DATASET, TARGET)
    await db_session.commit()
    assert await theme_trends_rerun_due(TARGET) is False
    assert (await refresh_theme_trends(TARGET))["status"] == "skipped"
    payload = await serving.get_theme_list(db_session, as_of_date=TARGET)
    assert payload["meta"]["attention_degraded"] is False


def test_unrankable_degraded_rows_are_left_out_of_the_attention_verdict():
    result = ScoringResult(
        theme_rows=[
            _theme_row("a", TARGET),
            _theme_row("new", TARGET, rankable=False, heat=None, rank_now=None, rank_trend=None,
                       attention_degraded=True),
        ],
        member_rows=[],
        company_rows=[],
        diagnostics={"attention_degraded": False, "attention_day_reasons": []},
    )
    report = evaluate_theme_trends(
        result, target=TARGET, active_tickers=["X.US"], priced_tickers=["X.US"],
        benchmark_priced=True, news_published=False,
    )
    assert report.passed and report.warnings == []
    assert report.metrics["attention_degraded"] is False
    assert report.metrics["attention_degraded_themes"] == []

    # A rankable degraded row still flags the run (and is the only one counted).
    result.theme_rows.append(_theme_row("b", TARGET, rank_now=2, rank_trend=2, attention_degraded=True))
    report = evaluate_theme_trends(
        result, target=TARGET, active_tickers=["X.US"], priced_tickers=["X.US"],
        benchmark_priced=True, news_published=True,
    )
    assert report.metrics["attention_degraded"] is True
    assert report.metrics["attention_degraded_themes"] == ["b"]
    assert report.warnings == [
        "Attention degraded on 2026-10-09; heat for 1 themes uses price, breadth and volume only"
    ]


@pytest.mark.asyncio
async def test_backfill_records_each_published_dates_own_quality_facts(db_session):
    dates = PRICE_SESSIONS[-5:]  # 2026-10-05 .. 2026-10-09
    # A partial UTC news day degrades attention for the sessions whose 7-day window holds it.
    await seed_world(db_session, incomplete_days=(date(2026, 9, 30),))
    await db_session.execute(delete(DailyPrice).where(DailyPrice.ticker.in_(AI[:2]), DailyPrice.date == dates[0]))
    await db_session.commit()

    result = await refresh_theme_trends(TARGET, backfill_sessions=5)

    assert result["status"] == "published" and result["published_dates"] == 5
    assert result["attention_degraded"] is False and result["dates_attention_degraded"] == 2
    run = await db_session.get(PipelineRun, result["run_id"])
    report = run.quality_report
    facts = report["metrics"]["dates"]
    assert set(facts) == {day.isoformat() for day in dates}

    first = facts[dates[0].isoformat()]
    assert first["attention_degraded"] is True and first["attention_degraded_themes"] == ["ai-compute", "grid-power"]
    assert "incomplete_news_days" in first["attention_reasons"]
    assert first["news_attention_published"] is False
    assert (first["active_member_tickers"], first["priced_member_tickers"], first["coverage_pct"]) == (16, 14, 87.5)
    assert any(warning.startswith("Attention degraded on 2026-10-05") for warning in first["warnings"])
    assert any("price coverage 87.5% on 2026-10-05 is below 90%" in warning for warning in first["warnings"])
    assert facts[dates[1].isoformat()]["attention_degraded"] is True
    assert facts[dates[1].isoformat()]["coverage_pct"] == 100.0
    for day in dates[2:4]:
        assert facts[day.isoformat()]["attention_degraded"] is False and facts[day.isoformat()]["warnings"] == []

    # The target's entry is the run-level report.
    target_facts = facts[TARGET.isoformat()]
    assert target_facts["attention_degraded"] is report["metrics"]["attention_degraded"] is False
    assert target_facts["coverage_pct"] == report["metrics"]["coverage_pct"] == 100.0
    assert target_facts["warnings"] == report["warnings"]
    assert target_facts["news_attention_published"] is True
    assert pipeline.published_date_facts(report, dates[0]) == first
    assert pipeline.published_date_facts(report, TARGET) == target_facts
    assert pipeline.published_date_facts(report, PRICE_SESSIONS[-6]) is None
    # Reports without per-date entries describe their own target only.
    legacy = {"metrics": {"as_of_date": TARGET.isoformat(), "attention_degraded": True}}
    assert pipeline.published_date_facts(legacy, TARGET)["attention_degraded"] is True
    assert pipeline.published_date_facts(legacy, dates[0]) is None

    # The attention re-run judges a backfilled date by that date's facts.
    assert await theme_trends_rerun_due(dates[0]) is False
    await _publish(db_session, NEWS_ATTENTION_DATASET, dates[0])
    await _publish(db_session, NEWS_ATTENTION_DATASET, dates[3])
    await db_session.commit()
    assert await theme_trends_rerun_due(dates[0]) is True
    assert await theme_trends_rerun_due(dates[3]) is False
    assert await theme_trends_rerun_due(TARGET) is False


@pytest.mark.asyncio
async def test_backfill_keeps_daily_publications_with_member_and_company_rows(db_session):
    older, middle = PRICE_SESSIONS[-3], PRICE_SESSIONS[-2]
    await seed_world(db_session, screener_days=(middle, TARGET))
    daily = await refresh_theme_trends(middle)
    daily_run = daily["run_id"]
    daily_members = await _count(db_session, ThemeMemberDaily, source_run_id=daily_run, date=middle)
    daily_companies = await _count(db_session, CompanyTrendDaily, source_run_id=daily_run, date=middle)
    assert daily_members == 16 and daily_companies > 0

    first = await refresh_theme_trends(TARGET, backfill_sessions=3)

    assert (first["published_dates"], first["kept_dates"]) == (2, 1)
    assert first["first_published_date"] == older.isoformat()
    assert await _publication_run(db_session, middle) == daily_run
    assert await _publication_run(db_session, older) == first["run_id"]
    assert await _publication_run(db_session, TARGET) == first["run_id"]
    # Nothing of the backfill run is stored for the kept date, and the daily rows survive.
    assert await _count(db_session, ThemeDaily, source_run_id=first["run_id"], date=middle) == 0
    assert await _count(db_session, ThemeDaily, source_run_id=daily_run, date=middle) == 2
    assert await _count(db_session, ThemeMemberDaily, source_run_id=daily_run, date=middle) == daily_members
    assert await _count(db_session, CompanyTrendDaily, source_run_id=daily_run, date=middle) == daily_companies
    assert first["theme_rows"] == 4
    run = await db_session.get(PipelineRun, first["run_id"])
    assert run.quality_report["metrics"]["kept_dates"] == [middle.isoformat()]
    assert set(run.quality_report["metrics"]["dates"]) == {older.isoformat(), TARGET.isoformat()}
    detail = await serving.get_theme_detail(db_session, "ai-compute", as_of_date=middle)
    assert len(detail["members"]) == 8
    companies = await serving.get_trending_companies(db_session, as_of_date=middle)
    assert len(companies["companies"]) == daily_companies

    # A re-run replaces the earlier backfill's dates but still keeps the daily one.
    second = await refresh_theme_trends(TARGET, backfill_sessions=3)
    assert (second["published_dates"], second["kept_dates"]) == (2, 1)
    assert await _publication_run(db_session, older) == second["run_id"]
    assert await _publication_run(db_session, middle) == daily_run
    assert await _count(db_session, ThemeDaily, source_run_id=first["run_id"]) == 0

    # overwrite=True republishes every date; the daily run's detail rows go with it.
    third = await refresh_theme_trends(TARGET, backfill_sessions=3, overwrite=True)
    assert (third["published_dates"], third["kept_dates"]) == (3, 0)
    assert await _publication_run(db_session, middle) == third["run_id"]
    assert await _count(db_session, ThemeMemberDaily, source_run_id=daily_run) == 0
    assert await _count(db_session, CompanyTrendDaily, source_run_id=daily_run) == 0


async def _tamper_served_rows(db, run_id: int, day: date, **values) -> list[ThemeDaily]:
    """Rewrite a published date's theme rows: a later run must continue them, not recompute."""
    rows = (await db.execute(
        select(ThemeDaily).where(ThemeDaily.source_run_id == run_id, ThemeDaily.date == day)
    )).scalars().all()
    for row in rows:
        row.stage = "leading"
        row.candidate_stage = "leading"
        row.rank_now = 1 if row.rank_now is not None else None
        for key, value in values.items():
            setattr(row, key, value)
    await db.commit()
    return rows


def _spy_scoring(monkeypatch) -> dict:
    seen = {}
    real_score = pipeline.score_themes

    def spy(inputs, *, published=None, **kwargs):
        seen["published"] = list(published or [])
        return real_score(inputs, published=published, **kwargs)

    monkeypatch.setattr(pipeline, "score_themes", spy)
    return seen


async def _target_rows(db, run_id: int) -> list[ThemeDaily]:
    return (await db.execute(
        select(ThemeDaily).where(ThemeDaily.source_run_id == run_id, ThemeDaily.date == TARGET)
    )).scalars().all()


@pytest.mark.asyncio
async def test_consecutive_daily_runs_continue_the_published_stage_history(db_session, monkeypatch):
    previous = PRICE_SESSIONS[-2]
    await seed_world(db_session, screener_days=(previous, TARGET))
    first = await refresh_theme_trends(previous)
    published = await _tamper_served_rows(db_session, first["run_id"], previous)
    seen = _spy_scoring(monkeypatch)

    second = await refresh_theme_trends(TARGET)

    assert second["status"] == "published"
    assert {(row["date"], row["stage"]) for row in seen["published"]} == {(previous, "leading")}
    run = await db_session.get(PipelineRun, second["run_id"])
    assert run.quality_report["metrics"]["published_history_rows"] == len(published)
    for row in await _target_rows(db_session, second["run_id"]):
        if row.rankable:
            assert row.stage_prev == "leading"


@pytest.mark.asyncio
async def test_published_history_spans_thirty_sessions(db_session, monkeypatch):
    previous = PRICE_SESSIONS[-2]
    await seed_world(db_session, screener_days=(previous, TARGET))
    first = await refresh_theme_trends(previous, backfill_sessions=31)  # publishes T-31..T-1
    assert (first["status"], first["published_dates"]) == ("published", 31)
    seen = _spy_scoring(monkeypatch)

    second = await refresh_theme_trends(TARGET)

    assert second["status"] == "published"
    # Exactly T-30..T-1: T-31 is published too but falls outside the window.
    assert {row["date"] for row in seen["published"]} == set(PRICE_SESSIONS[-31:-1])
    run = await db_session.get(PipelineRun, second["run_id"])
    assert run.quality_report["metrics"]["published_history_rows"] == len(THEMES) * 30


@pytest.mark.asyncio
async def test_backfill_continues_the_daily_publications_it_keeps(db_session, monkeypatch):
    middle = PRICE_SESSIONS[-2]
    await seed_world(db_session, screener_days=(middle, TARGET))
    daily = await refresh_theme_trends(middle)
    served = await _tamper_served_rows(db_session, daily["run_id"], middle, streak_top5=7)
    seen = _spy_scoring(monkeypatch)

    backfill = await refresh_theme_trends(TARGET, backfill_sessions=3)

    assert (backfill["status"], backfill["kept_dates"]) == ("published", 1)
    assert await _publication_run(db_session, middle) == daily["run_id"]
    # The kept date's served rows pin its session; the republished dates pin nothing.
    assert {(row["date"], row["stage"], row["streak_top5"]) for row in seen["published"]} == {(middle, "leading", 7)}
    metrics = (await db_session.get(PipelineRun, backfill["run_id"])).quality_report["metrics"]
    assert (metrics["published_history_rows"], metrics["pinned_kept_dates"]) == (len(served), 1)
    rows = [row for row in await _target_rows(db_session, backfill["run_id"]) if row.rankable]
    assert rows
    for row in rows:
        # The served "leading" holds unless today's candidate is emerging (which switches at once).
        assert row.stage_prev == "leading"
        assert row.stage == "leading" or row.candidate_stage == "emerging"
        assert row.stage_changed is (row.stage != "leading")
        assert row.rank_change == 1 - row.rank_now
        assert row.streak_top5 == (8 if row.rank_now <= 5 else 0)

    # A kept date whose publication changes between the source read and the write
    # fails the run instead of publishing rows chained to a record no longer served.
    real_detail = pipeline._dates_with_published_detail
    calls = []

    async def overwritten_meanwhile(db, dates):
        calls.append(list(dates))
        found = await real_detail(db, dates)
        return found if len(calls) == 1 else set()

    monkeypatch.setattr(pipeline, "_dates_with_published_detail", overwritten_meanwhile)
    with pytest.raises(RuntimeError, match="changed during the run"):
        await refresh_theme_trends(TARGET, backfill_sessions=3)
    failed = await db_session.scalar(
        select(PipelineRun).where(PipelineRun.pipeline_name == "theme_trends").order_by(PipelineRun.id.desc()).limit(1)
    )
    assert failed.status == "failed" and len(calls) == 2
    assert await _publication_run(db_session, TARGET) == backfill["run_id"]
    assert await _publication_run(db_session, middle) == daily["run_id"]
    assert await _count(db_session, ThemeDaily, source_run_id=failed.id) == 0


def _theme_row(slug, day, **overrides):
    row = {
        "theme_slug": slug, "date": day, "rankable": True, "heat": 60.0, "heat_now": 60.0, "heat_trend": 55.0,
        "rank_now": 1, "rank_trend": 1, "attention_degraded": False,
    }
    row.update(overrides)
    return row


def test_quality_gate_checks_heat_ranks_coverage_and_benchmark(monkeypatch):
    monkeypatch.setattr(settings, "PIPELINE_MIN_RANKABLE_THEMES", 2)
    monkeypatch.setattr(settings, "PIPELINE_MIN_THEME_PRICE_COVERAGE", 0.9)
    day = TARGET
    result = ScoringResult(
        theme_rows=[
            _theme_row("a", day),
            _theme_row("b", day, rank_trend=2, heat=None),
            _theme_row("c", day, rankable=False, heat=None, rank_now=None, rank_trend=None),
            _theme_row("a", day - timedelta(days=1), rank_now=None),
        ],
        member_rows=[],
        company_rows=[],
        diagnostics={"attention_degraded": False},
    )
    report = evaluate_theme_trends(
        result, target=day, active_tickers=["X.US", "Y.US"], priced_tickers=["X.US"],
        benchmark_priced=False, news_published=True,
    )
    errors = "\n".join(report.errors)
    assert not report.passed
    assert "price coverage 50.0%" in errors and "Y.US" in errors
    assert "SPY.US has no price" in errors
    assert "1 rankable theme rows have no heat: b@2026-10-09" in errors
    assert "Duplicate rank_now on 2026-10-09: [1]" in errors
    assert "1 rankable themes lack rank_now on 2026-10-08" in errors
    assert report.metrics["rankable_themes"] == 2 and report.metrics["coverage_pct"] == 50.0


def test_attention_problems_only_warn():
    result = ScoringResult(
        theme_rows=[_theme_row("a", TARGET, attention_degraded=True)],
        member_rows=[],
        company_rows=[],
        diagnostics={"attention_degraded": True, "attention_day_reasons": ["incomplete_news_days"]},
    )
    report = evaluate_theme_trends(
        result, target=TARGET, active_tickers=["X.US"], priced_tickers=["X.US"],
        benchmark_priced=True, news_published=False, metrics={"scoring_version": SCORING_VERSION},
    )
    assert report.passed and report.errors == []
    assert report.metrics["attention_degraded"] is True
    assert report.metrics["news_attention_published"] is False
    assert report.metrics["scoring_version"] == SCORING_VERSION
    assert any("incomplete_news_days" in warning for warning in report.warnings)
    assert any("news_attention is not published" in warning for warning in report.warnings)


def test_build_price_frames_uses_effective_close_and_keeps_gaps_nan():
    sessions = [date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7)]
    rows = [
        ("AAA.US", sessions[0], 10.0, 11.0, 100.0),
        ("AAA.US", sessions[1], None, 12.0, 200.0),  # no adjusted close -> raw close
        ("AAA.US", sessions[2], 0.0, 13.0, None),  # invalid adjusted close is not replaced
        ("SPY.US", sessions[0], 500.0, 500.0, 1.0),
        ("SPY.US", sessions[2], 505.0, 505.0, 1.0),
        ("ETF.US", sessions[1], 20.0, 20.0, 5.0),
    ]
    close, volume, benchmark, proxy = build_price_frames(rows, sessions, ["AAA.US", "BBB.US"], proxies=["ETF.US"])
    assert list(close.index) == sessions and list(close.columns) == ["AAA.US", "BBB.US"]
    assert close["AAA.US"].tolist()[:2] == [10.0, 12.0] and math.isnan(close["AAA.US"].iloc[2])
    assert close["BBB.US"].isna().all()
    assert volume["AAA.US"].tolist()[:2] == [100.0, 200.0] and math.isnan(volume["AAA.US"].iloc[2])
    assert benchmark.tolist()[0] == 500.0 and math.isnan(benchmark.iloc[1]) and benchmark.iloc[2] == 505.0
    assert list(proxy.columns) == ["ETF.US"] and proxy["ETF.US"].iloc[1] == 20.0
    assert "SPY.US" not in close.columns

    empty_close, _, empty_benchmark, empty_proxy = build_price_frames([], sessions, ["AAA.US"], proxies=["ETF.US"])
    assert empty_close.shape == (3, 1) and empty_close.isna().all().all()
    assert empty_benchmark.isna().all() and list(empty_proxy.columns) == ["ETF.US"]


@pytest.fixture
def backfill_script(monkeypatch):
    from scripts import backfill_theme_trends as script
    from services.eodhd_client import EODHDQuotaExceeded

    state = {"calls": [], "remaining": [], "trends": {"status": "published", "warnings": ["w"]}}

    async def fake_ingest(target, *, max_backfill_days=None):
        state["calls"].append(("news", target, max_backfill_days))
        value = state["remaining"].pop(0)
        if isinstance(value, Exception):
            raise value
        return {"status": "partial", "backfill_remaining": value}

    async def fake_trends(target, *, backfill_sessions=0, overwrite=False):
        state["calls"].append(("trends", target, backfill_sessions) + (("overwrite",) if overwrite else ()))
        return state["trends"]

    async def fake_init_db():
        return None

    monkeypatch.setattr(script, "ingest_market_news", fake_ingest)
    monkeypatch.setattr(script, "refresh_theme_trends", fake_trends)
    monkeypatch.setattr(script, "init_db", fake_init_db)
    state["script"] = script
    state["quota"] = EODHDQuotaExceeded
    return state


@pytest.mark.asyncio
async def test_backfill_script_batches_news_until_covered_then_backfills_trends(backfill_script):
    script = backfill_script["script"]
    backfill_script["remaining"] = [300, 180, 60, 0]
    code = await script.main(["--sessions", "7", "--target-date", "2026-10-10", "--news-batch-days", "120"])
    assert code == 0
    # A Saturday target resolves to Friday's session.
    assert backfill_script["calls"] == [("news", TARGET, 120)] * 4 + [("trends", TARGET, 7)]


@pytest.mark.asyncio
async def test_backfill_script_news_loop_stops_without_progress_or_quota(backfill_script):
    script = backfill_script["script"]
    backfill_script["remaining"] = [50, 50, 10]
    result = await script.backfill_news(TARGET, batch_days=10, max_batches=5)
    assert (result["stop_reason"], result["batches"], result["backfill_remaining"]) == ("no-progress", 2, 50)

    backfill_script["remaining"] = [40, backfill_script["quota"]("limit")]
    result = await script.backfill_news(TARGET, batch_days=10, max_batches=5)
    assert (result["stop_reason"], result["batches"], result["backfill_remaining"]) == ("quota-exhausted", 1, 40)

    backfill_script["remaining"] = [9, 8, 7]
    result = await script.backfill_news(TARGET, batch_days=1, max_batches=2)
    assert (result["stop_reason"], result["batches"]) == ("max-batches", 2)


@pytest.mark.asyncio
async def test_backfill_script_modes_and_exit_codes(backfill_script):
    script = backfill_script["script"]
    backfill_script["remaining"] = [0]
    assert await script.main(["--news-only", "--target-date", "2026-10-09"]) == 0
    assert [call[0] for call in backfill_script["calls"]] == ["news"]

    backfill_script["calls"].clear()
    backfill_script["trends"] = {"status": "deferred", "reason": "missing-publications", "missing": ["screener"]}
    assert await script.main(["--trends-only", "--target-date", "2026-10-09", "--sessions", "3"]) == 2
    assert backfill_script["calls"] == [("trends", TARGET, 3)]

    backfill_script["calls"].clear()
    backfill_script["trends"] = {"status": "published"}
    assert await script.main(["--trends-only", "--target-date", "2026-10-09", "--sessions", "3", "--overwrite"]) == 0
    assert backfill_script["calls"] == [("trends", TARGET, 3, "overwrite")]

    with pytest.raises(SystemExit):
        await script.main(["--news-only", "--trends-only"])


def test_backfill_script_default_target_uses_the_new_york_date(backfill_script, monkeypatch):
    from datetime import datetime, timezone

    script = backfill_script["script"]

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            # 01:30 UTC Wednesday is 21:30 Tuesday in New York: Tuesday's screener is not
            # published until 02:00 ET, so the default target is Monday.
            return datetime(2026, 10, 7, 1, 30, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(script, "datetime", FakeDatetime)
    assert script.resolve_target(None) == date(2026, 10, 5)
    assert script.resolve_target(date(2026, 10, 10)) == TARGET  # Saturday -> Friday
    assert script.resolve_target(date(2026, 10, 6)) == date(2026, 10, 6)
