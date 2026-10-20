import asyncio
import json
import math
from datetime import date, datetime, timedelta

import pytest
from pydantic import TypeAdapter
from sqlalchemy import select

from api.schemas import ThemeBriefResponse
from models import (
    DataPublication,
    NewsIngestDay,
    NewsItem,
    PipelineRun,
    Theme,
    ThemeBrief,
    ThemeDaily,
    ThemeMember,
    ThemeMemberDaily,
)
from services.ai_assistant import EvidenceCitationError, validate_evidence_numbers
from services.themes import briefs

AS_OF = date(2026, 10, 9)  # Friday


async def _theme(db, slug: str, **values) -> Theme:
    theme = Theme(
        slug=slug,
        name_en=values.pop("name_en", slug.replace("-", " ").title()),
        name_zh=values.pop("name_zh", f"{slug}题材"),
        megatrend="ai-infrastructure",
        definition=f"{slug} definition",
        keywords=["alpha"],
        negative_keywords=[],
        proxy_etfs=[],
        seed_core=[],
        pins=[],
        bans=[],
        status=values.pop("status", "active"),
        taxonomy_version="themes-v1",
    )
    db.add(theme)
    await db.flush()
    return theme


async def _run(db, target: date = AS_OF, *, status: str = "published", publish: bool = True) -> PipelineRun:
    run = PipelineRun(pipeline_name="theme_trends", target_date=target, status=status, stage=status,
                      version="theme-heat-v1")
    db.add(run)
    await db.flush()
    if publish:
        db.add(DataPublication(dataset="theme_trends", as_of_date=target, pipeline_run_id=run.id, status="published"))
    return run


def _day(run: PipelineRun, theme: Theme, session: date = AS_OF, **values) -> ThemeDaily:
    row = {
        "scoring_version": "theme-heat-v1", "pit": True, "rankable": True, "n_members": 8, "n_priced": 8,
        "stage": "neutral", "candidate_stage": "neutral", "heat": 60.0, "heat_now": 60.0, "heat_trend": 55.0,
        "x5": math.log(1.02), "x21": math.log(1.1), "x63": math.log(1.2), "b50": 0.72, "d_b50": 0.25, "nh": 0.4,
        "ign": 2, "ign5": 5, "rvol5_median": math.log(1.35), "att": 1.2, "att_breadth": 0.3,
        "score_price": 1.2, "score_breadth": 0.8, "score_volume": 0.4, "score_attention": 0.3,
    }
    row.update(values)
    return ThemeDaily(source_run_id=run.id, theme_id=theme.id, date=session, **row)


def _member_day(run: PipelineRun, theme: Theme, ticker: str, **values) -> ThemeMemberDaily:
    row = {"tier": "core", "weight": 0.15, "r1": 0.072, "x5": math.log(1.05), "x21": math.log(1.184),
           "pth": 0.98, "rvol5": math.log(3.1), "att_z": 2.1, "ignited": True,
           "first_ignition_date": date(2026, 10, 1), "leader_score": 0.9, "leader_rank": 1, "is_leader": True}
    row.update(values)
    return ThemeMemberDaily(source_run_id=run.id, theme_id=theme.id, ticker=ticker, date=AS_OF, **row)


def _news(url_hash: str, title: str, tickers: list[str], published_at: datetime, **values) -> NewsItem:
    return NewsItem(url_hash=url_hash, day=published_at.date(), published_at=published_at, title=title,
                    link=values.pop("link", f"https://example.com/{url_hash}"), source_domain="example.com",
                    tickers=tickers, symbol_count=len(tickers), **values)


def _memory_rows(run: PipelineRun, theme: Theme, **theme_values) -> list:
    values = {"rank_now": 1, "rank_change": 2, "stage": "accelerating", "stage_prev": "neutral", "stage_changed": True,
              "leaders": ["MU.US", "WDC.US"], **theme_values}
    return [
        _day(run, theme, **values),
        _member_day(run, theme, "MU.US"),
        _member_day(run, theme, "WDC.US", leader_rank=2, r1=-0.032, ignited=False, first_ignition_date=None),
        _member_day(run, theme, "STX.US", leader_rank=4, is_leader=False, ignited=False),
    ]


async def _republish(db, theme: Theme, **theme_values) -> PipelineRun:
    """A same-date re-run (the 07:50 attention re-run) that replaces the AS_OF publication."""
    rerun = await _run(db, publish=False)
    db.add_all(_memory_rows(rerun, theme, **theme_values))
    publication = (await db.execute(
        select(DataPublication).where(DataPublication.dataset == "theme_trends", DataPublication.as_of_date == AS_OF)
    )).scalar_one()
    publication.pipeline_run_id = rerun.id
    await db.commit()
    return rerun


async def _seed_memory_theme(db) -> dict:
    theme = await _theme(db, "memory-storage", name_zh="存储芯片")
    run = await _run(db)
    db.add_all(_memory_rows(run, theme))
    db.add(ThemeMember(theme_id=theme.id, ticker="SNDK.US", tier="core", weight=1.0, exposure=3, sources=["seed"],
                       review_status="verified", valid_from=date(2026, 9, 1)))
    db.add_all([
        _news("a", "Micron expands HBM capacity", ["MU.US"], datetime(2026, 10, 8, 14)),
        _news("b", "Micron expands HBM capacity", ["MU.US", "NVDA.US"], datetime(2026, 10, 8, 15)),  # duplicate title
        _news("c", "SanDisk wins flash supply contract", ["SNDK.US"], datetime(2026, 10, 9, 12)),
        _news("d", "Old Micron story", ["MU.US"], datetime(2026, 10, 5, 12)),  # outside 3 UTC days
        _news("e", "Unrelated bank merger", ["JPM.US"], datetime(2026, 10, 9, 13)),
    ])
    db.add_all([NewsIngestDay(day=AS_OF - timedelta(days=offset), status="complete") for offset in range(3)])
    await db.commit()
    return {"theme": theme, "run": run}


def _prompt_evidence(user_prompt: str) -> list[dict]:
    return json.loads(user_prompt.split("\n\n", 1)[0])["evidence"]


def _metric_ids(evidence: list[dict]) -> dict:
    """Theme facts by metric code, member facts by (ticker, metric)."""
    return {
        (item["ticker"], item["metric"]) if item["kind"] == "member_metric" else item["metric"]: item
        for item in evidence if item.get("metric")
    }


def _valid_payload(user_prompt: str) -> dict:
    evidence = _prompt_evidence(user_prompt)
    metrics = _metric_ids(evidence)
    heat, rank, x21, b50 = metrics["heat_now"], metrics["rank_now"], metrics["x21"], metrics["b50"]
    news = [item for item in evidence if item["kind"] in {"news", "news_absent"}]
    news_line = (f"同期新闻包括美光扩充 HBM 产能的报道。[{news[0]['id']}]" if news[0]["kind"] == "news"
                 else f"近期未检索到相关新闻标题。[{news[0]['id']}]")
    return {
        "facts": [
            f"题材热度 {heat['value']['display']}。[{heat['id']}]",
            f"题材热度排名第 {rank['value']['display']} 位。[{rank['id']}]",
            f"x21 篮子相对 SPY 超额 {x21['value']['display']}。[{x21['id']}]",
        ],
        "news": [news_line],
        "risks": [f"B50 为 {b50['value']['display']}，宽度扩散仍待确认。[{b50['id']}]"],
    }


@pytest.fixture
def deepseek_key(monkeypatch):
    monkeypatch.setattr(briefs.settings, "DEEPSEEK_API_KEY", "test-only")
    monkeypatch.setattr(briefs.settings, "THEME_BRIEFS_ENABLED", True)


def _fake_llm(calls: list, responder):
    async def fake(*, system_prompt, user_prompt, model=None, thinking=None, max_tokens=None):
        calls.append({"system": system_prompt, "user": user_prompt, "model": model, "thinking": thinking})
        return responder(user_prompt, len(calls))
    return fake


@pytest.mark.asyncio
async def test_briefs_are_disabled_without_key_or_flag(db_session, monkeypatch):
    await _seed_memory_theme(db_session)
    calls = []
    monkeypatch.setattr(briefs, "generate_deepseek_json", _fake_llm(calls, lambda *_: {}))
    monkeypatch.setattr(briefs.settings, "DEEPSEEK_API_KEY", "")
    assert (await briefs.generate_theme_briefs(AS_OF))["reason"] == "no-deepseek-key"
    monkeypatch.setattr(briefs.settings, "DEEPSEEK_API_KEY", "test-only")
    monkeypatch.setattr(briefs.settings, "THEME_BRIEFS_ENABLED", False)
    assert (await briefs.generate_theme_briefs(AS_OF))["reason"] == "disabled"
    assert calls == []


@pytest.mark.asyncio
async def test_unpublished_target_is_skipped(db_session, monkeypatch, deepseek_key):
    theme = await _theme(db_session, "quantum")
    failed = await _run(db_session, status="failed")
    db_session.add(_day(failed, theme, rank_now=1))
    await db_session.commit()
    calls = []
    monkeypatch.setattr(briefs, "generate_deepseek_json", _fake_llm(calls, lambda *_: {}))
    result = await briefs.generate_theme_briefs(AS_OF)
    assert result == {"status": "skipped", "reason": "theme-trends-not-published", "as_of_date": "2026-10-09"}
    assert calls == []


@pytest.mark.asyncio
async def test_validated_brief_is_cached_once_and_served(db_session, monkeypatch, deepseek_key):
    seeded = await _seed_memory_theme(db_session)
    calls = []
    monkeypatch.setattr(briefs, "generate_deepseek_json", _fake_llm(calls, lambda prompt, _: _valid_payload(prompt)))

    result = await briefs.generate_theme_briefs(AS_OF)
    assert result["status"] == "completed"
    assert (result["themes"], result["generated"], result["cached"], result["failed"]) == (1, 1, 0, 0)
    assert calls[0]["thinking"] is False and calls[0]["system"] == briefs.SYSTEM_PROMPT

    brief = (await db_session.execute(select(ThemeBrief))).scalar_one()
    assert brief.prompt_version == briefs.THEME_BRIEF_PROMPT_VERSION
    assert brief.validation_report["status"] == "validated"
    assert brief.validation_report["source_run_id"] == seeded["run"].id == result["source_run_id"]
    assert brief.content.index("**盘面事实**") < brief.content.index("**同期新闻**") < brief.content.index("**风险与反方**")
    assert "+10.00%" in brief.content and "60.0" in brief.content

    evidence = brief.evidence
    kinds = [item["kind"] for item in evidence]
    assert kinds.index("member_metric") > kinds.index("theme_metric")
    news = [item for item in evidence if item["kind"] == "news"]
    # Duplicate titles collapse; old and unrelated headlines are excluded; core members count.
    assert [item["label"] for item in news] == ["Micron expands HBM capacity", "SanDisk wins flash supply contract"]
    assert news[0]["link"] == "https://example.com/a"
    facts = _metric_ids(evidence)
    assert facts[("MU.US", "r1")]["value"] == {"display": "+7.20%"}
    assert facts[("MU.US", "first_ignition_date")]["value"] == {"display": "2026-10-01"}
    assert facts[("WDC.US", "r1")]["value"] == {"display": "-3.20%"}
    assert not any(item.get("ticker") == "STX.US" for item in evidence)  # neither a leader nor ignited today
    # One number per metric item, so a citation can only support that one value.
    numeric = [item for item in evidence if item["kind"] in briefs.METRIC_KINDS and item["metric"] != "stage"]
    assert numeric and all(list(item["value"]) == ["display"] for item in numeric)
    assert facts["heat_now"]["label"] == "题材热度 heat_now：60.0" and facts["heat_now"]["terms"] == ["热度", "heat_now"]
    assert facts["heat_trend"]["value"] == {"display": "55.0"} and facts["rank_change"]["value"] == {"display": "+2"}

    # Same evidence -> cache hit, no second LLM call.
    second = await briefs.generate_theme_briefs(AS_OF)
    assert (second["generated"], second["cached"]) == (0, 1)
    assert len(calls) == 1

    served = await briefs.latest_theme_brief(db_session, seeded["theme"].id, AS_OF + timedelta(days=3))
    validated = TypeAdapter(ThemeBriefResponse).validate_python({**served, "stale": True})
    assert validated.date == AS_OF and validated.content == brief.content
    assert validated.evidence[0].id == "E1"
    assert any(item.link == "https://example.com/a" for item in validated.evidence)
    assert await briefs.latest_theme_brief(db_session, seeded["theme"].id, AS_OF - timedelta(days=1)) is None


@pytest.mark.asyncio
async def test_invalid_bullet_is_dropped_and_recorded(db_session, monkeypatch, deepseek_key):
    await _seed_memory_theme(db_session)

    def partly_invalid(prompt, attempt):
        payload = _valid_payload(prompt)
        metrics = _metric_ids(_prompt_evidence(prompt))
        heat, rank = metrics["heat_now"], metrics["rank_now"]
        payload["facts"] = [line.replace("+10.00%", "+12.00%") for line in payload["facts"]]
        payload["facts"].append(
            f"题材热度 {heat['value']['display']}，排名第 {rank['value']['display']} 位。[{heat['id']}][{rank['id']}]")
        return payload

    calls = []
    monkeypatch.setattr(briefs, "generate_deepseek_json", _fake_llm(calls, partly_invalid))
    result = await briefs.generate_theme_briefs(AS_OF)
    # The fabricated and the two-citation bullets are dropped; the rest is kept on the first attempt.
    assert (result["generated"], result["fallback"]) == (1, 0) and len(calls) == 1
    brief = (await db_session.execute(select(ThemeBrief))).scalar_one()
    assert brief.model != briefs.TEMPLATE_MODEL and brief.validation_report["attempts"] == 1
    assert "+12.00%" not in brief.content and "x21" not in brief.content and "][E" not in brief.content
    assert "60.0" in brief.content and "同期新闻包括" in brief.content
    dropped = brief.validation_report["dropped_bullets"]
    assert len(dropped) == 2 and all(reason.startswith("盘面事实: ") for reason in dropped)
    assert any("+12.00%" in reason for reason in dropped)
    assert any("exactly one [E#] citation" in reason for reason in dropped)


@pytest.mark.asyncio
async def test_invalid_output_is_repaired_or_never_cached(db_session, monkeypatch, deepseek_key):
    await _seed_memory_theme(db_session)

    def fabricated(prompt, attempt):
        # The only risk bullet states a wrong B50, so the 风险与反方 section has nothing left.
        payload = _valid_payload(prompt)
        b50 = _metric_ids(_prompt_evidence(prompt))["b50"]
        payload["risks"] = [f"B50 为 99.9%，宽度扩散仍待确认。[{b50['id']}]"]
        payload["facts"] = [line.replace("+10.00%", "+12.00%") for line in payload["facts"]]
        return payload

    calls = []
    monkeypatch.setattr(briefs, "generate_deepseek_json", _fake_llm(calls, fabricated))
    result = await briefs.generate_theme_briefs(AS_OF)
    # Invalid model output is never cached; the validated template stands in.
    assert result["failed"] == 0 and result["fallback_slugs"] == ["memory-storage"]
    assert len(calls) == briefs.MAX_BRIEF_ATTEMPTS
    # One repair names every dropped bullet's problem, not only the first.
    repair = calls[1]["user"]
    assert "上一次输出未通过校验" in repair and "No valid bullet left in the 风险与反方 section" in repair
    assert "'99.9%'" in repair and "'+12.00%'" in repair
    template = (await db_session.execute(select(ThemeBrief))).scalar_one()
    assert template.model == briefs.TEMPLATE_MODEL and template.prompt_version == briefs.TEMPLATE_PROMPT_VERSION
    assert template.validation_report["fallback"] == "template"
    assert "99.9%" not in template.content and "[E" in template.content

    calls.clear()
    monkeypatch.setattr(briefs, "generate_deepseek_json", _fake_llm(
        calls, lambda prompt, attempt: fabricated(prompt, attempt) if attempt == 1 else _valid_payload(prompt)))
    result = await briefs.generate_theme_briefs(AS_OF)
    assert result["generated"] == 1 and len(calls) == 2
    brief = (await db_session.execute(
        select(ThemeBrief).where(ThemeBrief.model != briefs.TEMPLATE_MODEL))).scalar_one()
    assert brief.validation_report["attempts"] == 2 and brief.validation_report["dropped_bullets"] == []
    assert "99.9%" not in brief.content
    # The model brief written after the template is the one served.
    served = await briefs.latest_theme_brief(db_session, brief.theme_id, AS_OF)
    assert served["model"] == brief.model


@pytest.mark.asyncio
async def test_timeouts_retry_and_provider_errors_do_not(db_session, monkeypatch, deepseek_key):
    await _seed_memory_theme(db_session)
    monkeypatch.setattr(briefs.settings, "THEME_BRIEF_TIMEOUT_SECONDS", 0.01)
    calls = []

    async def slow(**kwargs):
        calls.append(kwargs)
        await asyncio.sleep(1)

    monkeypatch.setattr(briefs, "generate_deepseek_json", slow)
    assert (await briefs.generate_theme_briefs(AS_OF))["fallback"] == 1
    assert len(calls) == briefs.MAX_BRIEF_ATTEMPTS

    calls.clear()

    async def unavailable(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("provider down")

    monkeypatch.setattr(briefs, "generate_deepseek_json", unavailable)
    assert (await briefs.generate_theme_briefs(AS_OF))["fallback"] == 1
    assert len(calls) == 1
    stored = (await db_session.execute(select(ThemeBrief))).scalars().all()
    assert [brief.model for brief in stored] == [briefs.TEMPLATE_MODEL]


def test_template_brief_validates_and_refuses_without_metric_evidence():
    context = briefs.BriefContext(
        theme_id=1, slug="memory-storage", name_zh="存储", name_en="Memory", definition="d",
        as_of=date(2026, 10, 9), evidence=_evidence(), identity_strings=IDENTITIES,
    )
    content = briefs.template_brief(context)
    assert content is not None
    briefs.validate_theme_brief(content, context.evidence, identity_strings=IDENTITIES)
    assert "同期新闻包括 Micron expands HBM capacity。[E3]" in content
    assert "x21 篮子相对 SPY 超额收益：+18.40%。[E1]" in content

    bare = briefs.BriefContext(
        theme_id=1, slug="x", name_zh="x", name_en="x", definition="d", as_of=date(2026, 10, 9),
        evidence=[item for item in _evidence() if item["kind"] != "theme_metric"],
    )
    assert briefs.template_brief(bare) is None


@pytest.mark.asyncio
async def test_concurrent_writer_wins_and_is_adopted(db_session, monkeypatch, deepseek_key):
    seeded = await _seed_memory_theme(db_session)
    calls = []
    monkeypatch.setattr(briefs, "generate_deepseek_json", _fake_llm(calls, lambda prompt, _: _valid_payload(prompt)))
    assert (await briefs.generate_theme_briefs(AS_OF))["generated"] == 1

    async def stale_check(*args):
        return False  # As if another worker inserted between the check and the commit.

    monkeypatch.setattr(briefs, "_cached_brief_exists", stale_check)
    result = await briefs.generate_theme_briefs(AS_OF)
    assert (result["generated"], result["cached"], result["failed"]) == (0, 1, 0)
    rows = (await db_session.execute(select(ThemeBrief).where(ThemeBrief.theme_id == seeded["theme"].id))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_rerun_supersedes_the_brief_until_the_new_run_is_briefed(db_session, monkeypatch, deepseek_key):
    seeded = await _seed_memory_theme(db_session)
    theme = seeded["theme"]
    previous_run = await _run(db_session, AS_OF - timedelta(days=1))
    db_session.add(ThemeBrief(theme_id=theme.id, date=AS_OF - timedelta(days=1), evidence_hash="prev", model="m",
                              prompt_version=briefs.THEME_BRIEF_PROMPT_VERSION, evidence=[], content="previous session",
                              validation_report={"status": "validated", "source_run_id": previous_run.id}))
    await db_session.commit()
    calls = []
    monkeypatch.setattr(briefs, "generate_deepseek_json", _fake_llm(calls, lambda prompt, _: _valid_payload(prompt)))
    assert (await briefs.generate_theme_briefs(AS_OF))["generated"] == 1
    assert "60.0" in (await briefs.latest_theme_brief(db_session, theme.id, AS_OF))["content"]

    rerun = await _republish(db_session, theme, heat_now=65.0)
    # The AS_OF brief cites the replaced run; fall back to the older brief (serving marks it stale).
    fallback = await briefs.latest_theme_brief(db_session, theme.id, AS_OF)
    assert fallback["content"] == "previous session" and fallback["date"] == AS_OF - timedelta(days=1)

    result = await briefs.generate_theme_briefs(AS_OF)
    assert (result["generated"], result["source_run_id"]) == (1, rerun.id) and len(calls) == 2
    served = await briefs.latest_theme_brief(db_session, theme.id, AS_OF)
    assert served["date"] == AS_OF and "65.0" in served["content"] and "60.0" not in served["content"]


@pytest.mark.asyncio
async def test_identical_rerun_evidence_reuses_and_rebinds_the_cached_brief(db_session, monkeypatch, deepseek_key):
    seeded = await _seed_memory_theme(db_session)
    theme = seeded["theme"]
    calls = []
    monkeypatch.setattr(briefs, "generate_deepseek_json", _fake_llm(calls, lambda prompt, _: _valid_payload(prompt)))
    assert (await briefs.generate_theme_briefs(AS_OF))["generated"] == 1

    rerun = await _republish(db_session, theme)
    assert await briefs.latest_theme_brief(db_session, theme.id, AS_OF) is None  # no current brief yet

    result = await briefs.generate_theme_briefs(AS_OF)
    assert (result["generated"], result["cached"]) == (0, 1) and len(calls) == 1
    brief = (await db_session.execute(select(ThemeBrief))).scalar_one()
    await db_session.refresh(brief)
    assert brief.validation_report["source_run_id"] == rerun.id
    assert (await briefs.latest_theme_brief(db_session, theme.id, AS_OF))["content"] == brief.content


@pytest.mark.asyncio
async def test_targets_are_top_n_plus_lifecycle_events_from_the_published_run(db_session, monkeypatch, deepseek_key):
    monkeypatch.setattr(briefs.settings, "THEME_BRIEF_TOP_N", 2)
    themes = [await _theme(db_session, f"theme-{index}") for index in range(6)]
    run = await _run(db_session)
    superseded = await _run(db_session, status="failed", publish=False)
    db_session.add_all([
        _day(run, themes[0], rank_now=1),
        _day(run, themes[1], rank_now=2),
        _day(run, themes[2], rank_now=3),
        _day(run, themes[3], rank_now=4, stage="emerging", stage_prev="neutral", stage_changed=True,
             emerging_first_20=True),
        _day(run, themes[4], rankable=False, rank_now=None, stage="insufficient", stage_prev="neutral",
             stage_changed=True, heat_now=None),
        _day(superseded, themes[5], rank_now=1, emerging_first_20=True),
    ])
    await db_session.commit()
    contexts = await briefs.build_brief_contexts(db_session, AS_OF, run.id)
    assert [context.slug for context in contexts] == ["theme-0", "theme-1", "theme-3"]
    news = [item for item in contexts[0].evidence if item["kind"].startswith("news")]
    assert [item["kind"] for item in news] == ["news_absent"]
    assert "仅 0 个 UTC 日完整" in news[0]["label"]  # no complete news days in the window
    assert news[0]["value"]["complete_days"] == 0 and "articles" not in news[0]["value"]
    rankable = _metric_ids(contexts[0].evidence)["rankable_themes"]
    assert rankable["value"] == {"display": "4"} and contexts[0].evidence[0]["metric"] == "heat_now"
    assert contexts[0].source_run_id == run.id


def _evidence() -> list[dict]:
    return briefs.number_evidence([
        {"kind": "theme_metric", "metric": "x21", "label": "x21 篮子相对 SPY 超额收益：+18.40%",
         "source_date": "2026-10-09", "terms": ["x21"], "value": {"display": "+18.40%"}},
        {"kind": "member_metric", "metric": "r1", "ticker": "MU.US", "label": "MU（龙头） 当日涨跌 r1：-3.20%",
         "source_date": "2026-10-09", "terms": ["当日", "r1"], "value": {"display": "-3.20%"}},
        {"kind": "news", "label": "Micron expands HBM capacity", "source_date": "2026-10-08",
         "value": {"headline": "Micron expands HBM capacity"}},
        {"kind": "data_quality", "label": "新闻关注度降级", "source_date": "2026-10-09",
         "value": {"attention_degraded": True}},
        {"kind": "theme_metric", "metric": "heat_now", "label": "题材热度 heat_now：60.0", "source_date": "2026-10-09",
         "terms": ["热度", "heat_now"], "value": {"display": "60.0"}},
        {"kind": "theme_metric", "metric": "heat_trend", "label": "热度趋势 heat_trend：55.0",
         "source_date": "2026-10-09", "terms": ["热度趋势", "趋势热度", "heat_trend"], "value": {"display": "55.0"}},
        {"kind": "member_metric", "metric": "r1", "ticker": "WDC.US", "label": "WDC（龙头） 当日涨跌 r1：+7.20%",
         "source_date": "2026-10-09", "terms": ["当日", "r1"], "value": {"display": "+7.20%"}},
    ])


IDENTITIES = ("MU.US", "MU", "WDC.US", "WDC")


def _content(facts: str = "- x21 超额 +18.40%。[E1]", news: str = "- 同期新闻包括美光扩产报道。[E3]",
             risks: str = "- 新闻关注度降级。[E4]") -> str:
    return f"**盘面事实**\n{facts}\n\n**同期新闻**\n{news}\n\n**风险与反方**\n{risks}"


@pytest.mark.parametrize("content,message", [
    (_content().replace("**风险与反方**", "**风险**"), "Unexpected section"),
    ("**同期新闻**\n- 同期新闻包括美光扩产报道。[E3]\n\n**盘面事实**\n- x21 超额 +18.40%。[E1]\n\n**风险与反方**\n- 降级。[E4]",
     "in order"),
    (_content(facts="- x21 超额 +18.40%。[E1][E2]"), "exactly one"),
    (_content(facts="- x21 超额 +18.40% [E1]，且更高。"), "exactly one"),
    (_content(facts="- x21 超额 +18.40%。[E9]"), "Unknown evidence"),
    (_content(facts="- 同期新闻包括美光扩产报道。[E3]"), "not allowed"),
    (_content(news="- 美光扩产报道。[E3]"), "同期新闻包括"),
    (_content(news="- 同期新闻包括美光扩产报道，推动股价上涨。[E3]"), "caused"),
    (_content(facts="- x21 超额 +18.40%。成员走强。[E1]"), "one sentence"),
    (_content(risks="- 建议逢低买入。[E4]"), "trading advice"),
    (_content(facts="x21 超额 +18.40%。[E1]"), "bullet"),
    ("前言\n" + _content(), "before the first"),
    # Causal wording is refused in every section, and headline citations need the phrase everywhere.
    (_content(risks="- 美光下调指引导致题材热度回落并拖累成员股价。[E3]"), "caused"),
    (_content(risks="- 美光下调指引的报道削弱了热度结论。[E3]"), "同期新闻包括"),
    (_content(facts="- 受益于美光扩产新闻，x21 超额 +18.40%。[E1]"), "caused"),
    (_content(risks="- 由于新闻关注度降级，热度未计入关注度分量。[E4]"), "caused"),
    # Plain text only: the UI renders Markdown, so links and markup never get cached.
    (_content(news="- 同期新闻包括美光扩产报道，详见 [官方公告](https://evil.example/login)。[E3]"), "plain text"),
    (_content(risks="- 请访问 https://evil.example/claim 核实。[E4]"), "plain text"),
    (_content(risks="- 请访问 www.evil.example 核实。[E4]"), "plain text"),
    (_content(risks="- 新闻关注度 **重要** 降级。[E4]"), "plain text"),
    (_content(risks="- 联系 owner@evil.example 获取数据。[E4]"), "plain text"),
    (_content(risks="- 新闻关注度<b>降级</b>。[E4]"), "plain text"),
    (_content(risks="- 新闻关注度 `降级`。[E4]"), "plain text"),
    # Quote marks never exempt markup or extra sentences; only a news bullet may quote a headline.
    (_content(news="- 同期新闻包括“**重要** [官方](x)”的报道。[E3]"), "plain text"),
    (_content(facts="- x21 超额 +18.40%，“**重要**”。[E1]"), "plain text"),
    (_content(risks="- 新闻关注度降级“。另一句！”。[E4]"), "one sentence"),
    # A cited fact binds one value to one metric and one subject.
    (_content(facts="- 题材热度 55.0。[E6]"), "热度趋势"),
    (_content(facts="- 热度趋势 60.0。[E5]"), "热度"),
    (_content(facts="- 超额 +18.40%。[E1]"), "x21"),
    (_content(facts="- WDC 当日 -3.20%。[E2]"), "must name that ticker"),
    (_content(facts="- MU 与 WDC 当日 -3.20%。[E2]"), "other members"),
    (_content(facts="- MU 的 x21 超额 +18.40%。[E1]"), "theme-level"),
    (_content(facts="- WDC 当日下跌 7.20%。[E7]"), "minus sign"),
    (_content(news="- 同期新闻包括美光将 HBM 产能提高 2 倍的报道。[E3]"), "restate its numbers"),
])
def test_section_and_citation_validator_rejects(content, message):
    with pytest.raises(EvidenceCitationError, match=message):
        briefs.validate_theme_brief(content, _evidence(), identity_strings=IDENTITIES)


def test_validator_accepts_a_verbatim_unquoted_headline_with_its_punctuation_and_numbers():
    """Live DeepSeek output copies English headlines verbatim, without quote marks."""
    evidence = briefs.number_evidence([
        {"kind": "theme_metric", "metric": "x21", "label": "x21 篮子相对 SPY 超额收益：+18.40%",
         "source_date": "2026-10-09", "terms": ["x21"], "value": {"display": "+18.40%"}},
        {"kind": "news", "label": "Quanta vs. MasTec: Is Better Cash Flow Worth a 73% Earnings Premium?",
         "source_date": "2026-10-08",
         "value": {"headline": "Quanta vs. MasTec: Is Better Cash Flow Worth a 73% Earnings Premium?"}},
        {"kind": "data_quality", "label": "新闻关注度降级", "source_date": "2026-10-09",
         "value": {"attention_degraded": True}},
    ])
    content = (
        "**盘面事实**\n- x21 超额 +18.40%。[E1]\n\n"
        "**同期新闻**\n- 同期新闻包括 Quanta vs. MasTec: Is Better Cash Flow Worth a 73% Earnings Premium?。[E2]\n\n"
        "**风险与反方**\n- 新闻关注度降级。[E3]"
    )
    # The composed paths that cache briefs: "73%" sits next to a metric word ("Cash Flow"), so a
    # raw numeric check would refuse the headline and the template fallback would fail too.
    context = briefs.BriefContext(theme_id=1, slug="s", name_zh="z", name_en="e", definition="d", as_of=AS_OF,
                                  evidence=evidence)
    payload = {"facts": ["x21 超额 +18.40%。[E1]"],
               "news": ["同期新闻包括 Quanta vs. MasTec: Is Better Cash Flow Worth a 73% Earnings Premium?。[E2]"],
               "risks": ["新闻关注度降级。[E3]"]}
    assert briefs.validated_brief_content(payload, context) == content
    assert briefs.template_brief(context) is not None
    # The numeric validator still sees every number outside the headline.
    assert "+18.40%" in briefs._numbers_view(content, evidence)
    assert "73%" not in briefs._numbers_view(content, evidence)

    # Outside the verbatim headline, restated numbers and extra sentences are still refused.
    with pytest.raises(EvidenceCitationError, match="restate its numbers"):
        briefs.validate_theme_brief(content.replace("Premium?。[E2]", "Premium? 溢价达 73%。[E2]"), evidence)
    with pytest.raises(EvidenceCitationError, match="one sentence"):
        briefs.validate_theme_brief(content.replace("Premium?。[E2]", "Premium?。值得关注。[E2]"), evidence)


@pytest.mark.asyncio
async def test_theme_names_with_digits_are_identities_not_numbers(db_session):
    """The shipped glp1-obesity theme: "GLP-1" is a name, while restated values are still refused."""
    theme = await _theme(db_session, "glp1-obesity", name_zh="GLP-1 / 减重", name_en="GLP-1 & Obesity")
    run = await _run(db_session)
    db_session.add_all([
        _day(run, theme, rank_now=1, leaders=["LLY.US"]),
        _member_day(run, theme, "LLY.US"),
        _news("g", "Eli Lilly raises outlook on obesity drug demand", ["LLY.US"], datetime(2026, 10, 9, 12)),
    ])
    db_session.add_all([NewsIngestDay(day=AS_OF - timedelta(days=offset), status="complete") for offset in range(3)])
    await db_session.commit()
    [context] = await briefs.build_brief_contexts(db_session, AS_OF, run.id)
    assert "GLP-1" in context.identity_strings
    assert not any(value.isdigit() for value in context.identity_strings)
    heat = _metric_ids(context.evidence)["heat_now"]
    news_id = next(item["id"] for item in context.evidence if item["kind"] == "news")

    def payload(facts: str, news: str = f"同期新闻包括礼来 GLP-1 药物销售增长的报道。[{news_id}]") -> dict:
        return {"facts": [facts], "news": [news], "risks": [f"glp-1 题材热度结论仍待确认。[{heat['id']}]"]}

    assert briefs.validated_brief_content(payload(f"GLP-1 题材热度 {heat['value']['display']}。[{heat['id']}]"), context)
    assert briefs.validated_brief_content(payload(f"GLP-1 题材处于加速阶段。[{heat['id']}]"), context)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim '61.0'"):
        briefs.validated_brief_content(payload(f"GLP-1 题材热度 61.0。[{heat['id']}]"), context)
    with pytest.raises(EvidenceCitationError, match="restate its numbers"):
        briefs.validated_brief_content(payload(
            f"GLP-1 题材热度 {heat['value']['display']}。[{heat['id']}]",
            f"同期新闻包括礼来 GLP-1 药物销售增长 20% 的报道。[{news_id}]",
        ), context)


def test_validator_accepts_the_contract_and_numbers_must_be_copied_signed():
    evidence = _evidence()
    briefs.validate_theme_brief(_content(), evidence)
    briefs.validate_theme_brief(_content(news="- 同期新闻包括“Is HBM sold out? Micron expands”的报道。[E3]"), evidence)
    briefs.validate_theme_brief(_content(risks="- 同期新闻包括美光扩产报道，热度结论仍待确认。[E3]"), evidence)
    validate_evidence_numbers(_content(), evidence)
    # An unsigned negative ("下跌 3.20%") is refused before the number check sees it.
    with pytest.raises(EvidenceCitationError, match="minus sign"):
        briefs.validate_theme_brief(_content(facts="- MU.US 当日下跌 3.20%。[E2]"), evidence, identity_strings=IDENTITIES)
    with pytest.raises(EvidenceCitationError, match="Unsupported numeric claim"):
        validate_evidence_numbers(_content(facts="- MU.US 当日 3.20%。[E2]"), evidence, identity_strings=IDENTITIES)
    signed = _content(facts="- MU.US 当日 -3.20%。[E2]")
    briefs.validate_theme_brief(signed, evidence, identity_strings=IDENTITIES)
    validate_evidence_numbers(signed, evidence, identity_strings=IDENTITIES)
    # Longest-match metric words: 热度趋势 is not 热度, yet 热度排名-style compounds still count.
    trend = _content(facts="- 热度趋势 55.0，题材热度仍更高。[E6]")
    briefs.validate_theme_brief(trend, evidence)
    validate_evidence_numbers(trend, evidence)
    # Qualitative bullets need no metric word; member names without a value are fine.
    briefs.validate_theme_brief(_content(facts="- MU 与 WDC 同为题材龙头。[E2]"), evidence, identity_strings=IDENTITIES)


def test_field_swaps_from_one_run_are_rejected_end_to_end():
    """The reviewer's swapped-field claims against evidence built from a real theme row."""
    row = ThemeDaily(date=AS_OF, heat_now=60.0, heat_trend=55.0, rank_now=1, rank_change=2, streak_top5=3,
                     stage="accelerating", stage_prev="neutral", stage_changed=True, emerging_first_20=False,
                     x5=math.log(1.02), x21=math.log(1.1), b50=0.72, d_b50=0.25, nh=0.4, ign=2, ign5=5, n_members=8,
                     n_priced=8, score_price=1.2, score_breadth=0.8, score_volume=-0.4, score_attention=0.3)
    evidence = briefs.number_evidence([
        *briefs._theme_metric_items(row, 12),
        {"kind": "news", "label": "Micron expands HBM capacity", "source_date": "2026-10-08",
         "value": {"headline": "Micron expands HBM capacity"}},
    ])
    ids = {key: item["id"] for key, item in _metric_ids(evidence).items()}
    news_id = evidence[-1]["id"]

    def brief(line: str) -> str:
        return (f"**盘面事实**\n- {line}\n\n**同期新闻**\n- 同期新闻包括美光扩产报道。[{news_id}]\n\n"
                f"**风险与反方**\n- B50 为 72.0%。[{ids['b50']}]")

    def accepted(line: str) -> bool:
        try:
            briefs.validate_theme_brief(brief(line), evidence)
            validate_evidence_numbers(brief(line), evidence)
        except EvidenceCitationError:
            return False
        return True

    for line in (f"题材热度 60.0。[{ids['heat_now']}]", f"热度趋势 55.0。[{ids['heat_trend']}]",
                 f"题材热度排名第 1 位。[{ids['rank_now']}]", f"共有 12 个可排名题材。[{ids['rankable_themes']}]",
                 f"已连续 3 个交易日位列前五。[{ids['streak_top5']}]", f"NH 为 40.0%。[{ids['nh']}]",
                 f"IGN5 为 5。[{ids['ign5']}]", f"量能分量 -0.40。[{ids['score_volume']}]"):
        assert accepted(line), line
    false_claims = [
        f"题材排名第 12 位。[{ids['rank_now']}]",  # 12 is the number of rankable themes
        f"题材排名第 12 位。[{ids['rankable_themes']}]",
        f"题材热度 55.0。[{ids['heat_now']}]",  # 55.0 is heat_trend
        f"题材热度 55.0。[{ids['heat_trend']}]",
        f"热度趋势 60.0。[{ids['heat_now']}]",
        f"已连续 1 个交易日位列前五。[{ids['streak_top5']}]",
        f"价格分量 +0.30，关注度分量 +1.20。[{ids['score_price']}]",  # swapped components
        f"价格分量 +0.30，关注度分量 +1.20。[{ids['score_attention']}]",
        f"关注度分量 +1.20。[{ids['score_price']}]",
        f"NH 为 72.0%，B50 为 40.0%。[{ids['b50']}]",  # swapped breadth fields
        f"NH 为 72.0%，B50 为 40.0%。[{ids['nh']}]",
        f"NH 为 72.0%。[{ids['b50']}]",
        f"IGN5 为 8。[{ids['ign5']}]",  # 8 is n_members
        f"IGN5 为 8。[{ids['n_members']}]",
        f"x21 篮子超额 +2.00%。[{ids['x5']}]",
    ]
    assert [claim for claim in false_claims if accepted(claim)] == []


def test_markup_in_model_output_is_never_cached():
    context = briefs.BriefContext(theme_id=1, slug="memory", name_zh="存储", name_en="Memory", definition="",
                                  as_of=AS_OF, evidence=_evidence(), identity_strings=IDENTITIES)
    payload = {"facts": ["x21 超额 +18.40%。[E1]"],
               "news": ["同期新闻包括美光扩产报道，详见 [官方公告](https://evil.example/login)。[E3]"],
               "risks": ["请访问 https://evil.example/claim 核实 **重要**。[E4]"]}
    with pytest.raises(EvidenceCitationError, match="plain text"):
        briefs.validated_brief_content(payload, context)


def test_bullets_are_normalized_before_validation():
    sections = briefs._BriefSections(facts=["- x21 超额 +18.40%[E1]。"], news=["1. 同期新闻包括美光扩产报道。[E3]"],
                                     risks=["新闻关注度降级。[E4]"])
    content = briefs.render_brief(sections)
    assert "- x21 超额 +18.40%。[E1]" in content
    assert "- 同期新闻包括美光扩产报道。[E3]" in content
    briefs.validate_theme_brief(content, _evidence())
    signed = briefs.render_brief(briefs._BriefSections(facts=["-3.20% 为 MU.US 当日表现。[E2]"],
                                                       news=["同期新闻包括美光扩产报道。[E3]"], risks=["降级。[E4]"]))
    assert "- -3.20% 为" in signed


def test_brief_hash_covers_prompt_version_model_and_evidence(monkeypatch):
    evidence = _evidence()
    first = briefs.build_theme_brief_hash(slug="memory", as_of=AS_OF, evidence=evidence, model="m1")
    assert first == briefs.build_theme_brief_hash(slug="memory", as_of=AS_OF, evidence=evidence, model="m1")
    assert first != briefs.build_theme_brief_hash(slug="memory", as_of=AS_OF, evidence=evidence, model="m2")
    assert first != briefs.build_theme_brief_hash(slug="memory", as_of=AS_OF, evidence=evidence[:2], model="m1")
    monkeypatch.setattr(briefs, "THEME_BRIEF_PROMPT_VERSION", "theme-brief-test")
    assert first != briefs.build_theme_brief_hash(slug="memory", as_of=AS_OF, evidence=evidence, model="m1")


@pytest.mark.asyncio
async def test_latest_brief_picks_newest_on_or_before(db_session):
    theme = await _theme(db_session, "grid")
    for day, content in ((date(2026, 10, 7), "old"), (date(2026, 10, 8), "mid"), (date(2026, 10, 12), "future")):
        db_session.add(ThemeBrief(theme_id=theme.id, date=day, evidence_hash=content, model="m",
                                  prompt_version="theme-brief-v1", evidence=[{"id": "E1", "kind": "news", "label": "t"}],
                                  content=content))
    await db_session.commit()
    brief = await briefs.latest_theme_brief(db_session, theme.id, AS_OF)
    assert brief["content"] == "mid" and brief["date"] == date(2026, 10, 8)
    assert brief["evidence"] == [{"id": "E1", "kind": "news", "label": "t", "source_date": None, "link": None}]
