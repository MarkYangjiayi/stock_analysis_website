import json
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from models import DailyPrice, NewsItem, Theme, ThemeMember, ThemeProposal, Ticker
from services.themes import proposals
from services.themes.scoring import attention_star
from services.themes.universe import CompanyProfile

AS_OF = date(2026, 10, 9)  # Friday
CLUSTER = ["AAA.US", "BBB.US", "CCC.US"]
SESSIONS = proposals._sessions_through(AS_OF, proposals.PRICE_SESSIONS)


def _paths() -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Daily returns and volumes per ticker over SESSIONS (first return unused)."""
    rng = np.random.RandomState(7)
    n = len(SESSIONS)
    factor = rng.normal(0, 0.02, n)
    ignitions = [n - 3, n - 7]
    paths = {}
    for ticker in [*CLUSTER, "MAPPED.US", "DDD.US"]:
        returns = factor + rng.normal(0, 0.003, n)
        volume = np.full(n, 1_000_000.0)
        if ticker != "DDD.US":  # correlated but never ignites: not a mover
            returns[ignitions] = 0.06
            volume[ignitions] = 3_000_000.0
        paths[ticker] = (returns, volume)
    noise = rng.normal(0, 0.02, n)
    noise_volume = np.full(n, 1_000_000.0)
    noise[[n - 2, n - 5]] = 0.07
    noise_volume[[n - 2, n - 5]] = 4_000_000.0
    paths["NOISE.US"] = (noise, noise_volume)
    paths["QUIET.US"] = (rng.normal(0, 0.01, n), np.full(n, 1_000_000.0))
    return paths


UNIVERSE = {
    ticker: CompanyProfile(ticker=ticker, name=f"{ticker[:3].title()} Corp", sector="Industrials",
                           industry="Electrical Equipment", description=f"{ticker[:3]} makes grid transformers.",
                           market_cap=1e9)
    for ticker in [*CLUSTER, "MAPPED.US", "DDD.US", "NOISE.US", "QUIET.US"]
}


async def _seed(db) -> Theme:
    for ticker, (returns, volume) in _paths().items():
        db.add(Ticker(ticker=ticker))
        price = 100.0
        for index, session in enumerate(SESSIONS):
            if index:
                price *= 1 + returns[index]
            db.add(DailyPrice(ticker=ticker, date=session, open=price, high=price, low=price, close=price,
                              adjusted_close=price, volume=int(volume[index])))
    theme = Theme(slug="grid", name_en="Grid", name_zh="电网", megatrend=None, definition="d", keywords=[],
                  negative_keywords=[], proxy_etfs=[], seed_core=[], pins=[], bans=[], status="active",
                  taxonomy_version="themes-v1")
    db.add(theme)
    await db.flush()
    db.add(ThemeMember(theme_id=theme.id, ticker="MAPPED.US", tier="core", weight=1.0, exposure=3, sources=["seed"],
                       review_status="verified", valid_from=date(2026, 9, 1)))
    db.add_all([
        NewsItem(url_hash="n1", day=date(2026, 10, 7), published_at=datetime(2026, 10, 7, 14),
                 title="Aaa and Bbb win utility transformer orders", link="https://example.com/n1",
                 source_domain="example.com", tickers=["AAA.US", "BBB.US"], symbol_count=2),
        NewsItem(url_hash="n2", day=date(2026, 9, 1), published_at=datetime(2026, 9, 1, 14),
                 title="Old Aaa story", link="https://example.com/n2", source_domain="example.com",
                 tickers=["AAA.US"], symbol_count=1),
    ])
    await db.commit()
    return theme


@pytest.fixture
def universe(monkeypatch):
    async def fake_universe(db, as_of):
        return AS_OF, dict(UNIVERSE)
    monkeypatch.setattr(proposals, "load_theme_universe", fake_universe)


def _naming(calls: list, *, valid=lambda attempt: True):
    async def fake(*, system_prompt, user_prompt, model=None, thinking=None, max_tokens=None):
        calls.append(user_prompt)
        evidence = json.loads(user_prompt.split("\n\n", 1)[0])["evidence"]
        news = next(item["id"] for item in evidence if item["kind"] == "news")
        rationale = (f"These companies supply grid transformers to utilities [E1], and saved headlines "
                     f"cover new utility orders [{news}].")
        return {"label_en": "Grid Transformers", "label_zh": "电网变压器", "slug_suggestion": "Grid Transformers!",
                "rationale": rationale if valid(len(calls)) else "Unsupported grouping without any evidence at all."}
    return fake


@pytest.mark.asyncio
async def test_unmapped_correlated_movers_become_one_named_proposal(db_session, monkeypatch, universe):
    await _seed(db_session)
    monkeypatch.setattr(proposals.settings, "DEEPSEEK_API_KEY", "test-only")
    calls = []
    monkeypatch.setattr(proposals, "generate_deepseek_json", _naming(calls))

    result = await proposals.refresh_theme_proposals(AS_OF)
    assert result["status"] == "completed"
    assert result["unmapped"] == 6 and result["movers"] == 4  # cluster + NOISE; MAPPED excluded, DDD quiet
    assert (result["clusters"], result["created"], result["named"]) == (1, 1, 1)
    proposal = (await db_session.execute(select(ThemeProposal))).scalar_one()
    assert proposal.tickers == CLUSTER and proposal.status == "pending"
    assert (proposal.label_en, proposal.label_zh, proposal.slug_suggestion) == (
        "Grid Transformers", "电网变压器", "grid-transformers")
    assert proposal.prompt_version == proposals.PROPOSAL_PROMPT_VERSION
    assert (proposal.first_seen, proposal.last_seen, proposal.hit_count) == (AS_OF, AS_OF, 1)
    kinds = [item["kind"] for item in proposal.evidence]
    assert kinds == ["company", "company", "company", "news"]
    assert proposal.evidence[-1]["link"] == "https://example.com/n1"
    assert "[E4]" in proposal.rationale

    # Same session again: merged, not double counted, not renamed.
    again = await proposals.refresh_theme_proposals(AS_OF)
    assert (again["created"], again["updated"], again["named"]) == (0, 1, 0)
    assert len(calls) == 1
    await db_session.refresh(proposal)
    assert proposal.hit_count == 1

    proposal.last_seen = date(2026, 10, 2)
    await db_session.commit()
    await proposals.refresh_theme_proposals(AS_OF)
    await db_session.refresh(proposal)
    assert (proposal.hit_count, proposal.last_seen) == (2, AS_OF)


@pytest.mark.asyncio
async def test_decided_groups_are_suppressed_and_stale_pending_expire(db_session, monkeypatch, universe):
    await _seed(db_session)
    monkeypatch.setattr(proposals.settings, "DEEPSEEK_API_KEY", "")
    stale_seen = proposals._sessions_through(AS_OF, 22)[0]
    db_session.add_all([
        ThemeProposal(proposal_key="rejected", tickers=["AAA.US", "BBB.US", "CCC.US", "ZZZ.US"], first_seen=stale_seen,
                      last_seen=stale_seen, status="rejected"),
        ThemeProposal(proposal_key="stale", tickers=["OLD1.US", "OLD2.US", "OLD3.US"], first_seen=stale_seen,
                      last_seen=stale_seen, status="pending"),
        ThemeProposal(proposal_key="recent", tickers=["NEW1.US", "NEW2.US", "NEW3.US"], first_seen=date(2026, 10, 1),
                      last_seen=date(2026, 10, 1), status="pending"),
    ])
    await db_session.commit()
    result = await proposals.refresh_theme_proposals(AS_OF)
    assert (result["suppressed"], result["created"], result["expired"]) == (1, 0, 1)
    statuses = dict((await db_session.execute(select(ThemeProposal.proposal_key, ThemeProposal.status))).all())
    assert statuses == {"rejected": "rejected", "stale": "expired", "recent": "pending"}


@pytest.mark.asyncio
async def test_without_key_or_valid_name_proposals_stay_unnamed_until_named(db_session, monkeypatch, universe):
    await _seed(db_session)
    monkeypatch.setattr(proposals.settings, "DEEPSEEK_API_KEY", "")
    calls = []
    monkeypatch.setattr(proposals, "generate_deepseek_json", _naming(calls))
    result = await proposals.refresh_theme_proposals(AS_OF)
    assert (result["created"], result["named"]) == (1, 0) and calls == []
    proposal = (await db_session.execute(select(ThemeProposal))).scalar_one()
    assert proposal.label_en is None and proposal.rationale is None and proposal.llm_model is None
    assert len(proposal.evidence) == 4

    monkeypatch.setattr(proposals.settings, "DEEPSEEK_API_KEY", "test-only")
    monkeypatch.setattr(proposals, "generate_deepseek_json", _naming(calls, valid=lambda attempt: False))
    failed = await proposals.refresh_theme_proposals(AS_OF)
    assert failed["naming_failed"] == 1 and len(calls) == proposals.MAX_NAMING_ATTEMPTS
    assert "failed validation" in calls[1]
    await db_session.refresh(proposal)
    assert proposal.label_en is None

    calls.clear()
    monkeypatch.setattr(proposals, "generate_deepseek_json", _naming(calls))
    named = await proposals.refresh_theme_proposals(AS_OF)
    assert (named["updated"], named["named"]) == (1, 1)
    await db_session.refresh(proposal)
    assert proposal.slug_suggestion == "grid-transformers" and proposal.hit_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", "test-only"])
async def test_named_proposal_whose_tickers_drift_is_renamed_or_unnamed(db_session, monkeypatch, universe, key):
    await _seed(db_session)
    db_session.add(ThemeProposal(
        proposal_key="old", tickers=["AAA.US", "BBB.US", "CCC.US", "ZZZ.US"], first_seen=date(2026, 10, 2),
        last_seen=date(2026, 10, 2), status="pending", label_en="Old Label", label_zh="旧名", slug_suggestion="old",
        rationale="Written for ZZZ and friends [E1].", llm_model="m", prompt_version=proposals.PROPOSAL_PROMPT_VERSION,
        evidence=[{"id": "E1", "kind": "company", "label": "ZZZ.US"}],
    ))
    await db_session.commit()
    monkeypatch.setattr(proposals.settings, "DEEPSEEK_API_KEY", key)
    calls = []
    monkeypatch.setattr(proposals, "generate_deepseek_json", _naming(calls))

    result = await proposals.refresh_theme_proposals(AS_OF)
    assert (result["created"], result["updated"]) == (0, 1)
    proposal = (await db_session.execute(select(ThemeProposal))).scalar_one()
    assert proposal.tickers == CLUSTER and proposal.hit_count == 2
    assert [item["label"].split()[0] for item in proposal.evidence if item["kind"] == "company"] == CLUSTER
    if key:
        assert len(calls) == 1 and result["named"] == 1
        assert (proposal.label_en, proposal.slug_suggestion) == ("Grid Transformers", "grid-transformers")
    else:
        # The old rationale described another ticker set; it is cleared until the group is re-named.
        assert calls == [] and proposal.label_en is None and proposal.rationale is None and proposal.llm_model is None


@pytest.mark.asyncio
async def test_a_pending_proposal_absorbs_at_most_one_cluster_per_run(db_session, monkeypatch, universe):
    first, second = ["AAA.US", "BBB.US", "CCC.US"], ["DDD.US", "NOISE.US", "QUIET.US"]
    db_session.add(ThemeProposal(proposal_key="both", tickers=sorted(first + second), first_seen=date(2026, 10, 2),
                                 last_seen=date(2026, 10, 2), status="pending"))
    await db_session.commit()
    monkeypatch.setattr(proposals.settings, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(proposals, "find_movers", lambda *args, **kwargs: {ticker: 2 for ticker in first + second})
    monkeypatch.setattr(proposals, "correlation_clusters", lambda *args, **kwargs: [first, second])
    assert proposals.jaccard(set(first), set(first + second)) == proposals.MERGE_JACCARD

    result = await proposals.refresh_theme_proposals(AS_OF)
    assert (result["created"], result["updated"]) == (1, 1)
    rows = dict((await db_session.execute(select(ThemeProposal.proposal_key, ThemeProposal.tickers))).all())
    assert rows.pop("both") == first
    assert list(rows.values()) == [second]


@pytest.mark.asyncio
async def test_no_screener_universe_is_skipped(db_session, monkeypatch):
    async def empty(db, as_of):
        return None, {}
    monkeypatch.setattr(proposals, "load_theme_universe", empty)
    assert await proposals.refresh_theme_proposals(date(2026, 10, 10)) == {
        "status": "skipped", "reason": "no-screener", "as_of_date": "2026-10-09"}


def test_find_movers_counts_recent_ignitions_and_attention():
    index = pd.Index(SESSIONS, dtype=object)
    n = len(index)
    close = pd.DataFrame(100.0, index=index, columns=["A.US", "B.US", "C.US", "D.US", "E.US", "F.US", "G.US"])
    volume = pd.DataFrame(1_000_000.0, index=index, columns=close.columns)

    def ignite(column: str, position: int) -> None:
        close.iloc[position:, close.columns.get_loc(column)] *= 1.06
        volume.iloc[position, volume.columns.get_loc(column)] = 3_000_000.0

    for position in (n - 2, n - 4):  # A ignites twice recently
        ignite("A.US", position)
    ignite("B.US", n - 2)  # B once recently ...
    ignite("B.US", n - 15)  # ... and once outside the 10-session window
    close.iloc[n - 3:, 2] *= 1.08  # C moves without volume
    ignite("F.US", n - 2)
    ignite("G.US", n - 2)
    recent = index[-10:]
    assert (recent[6] - recent[1]).days == 7 and (recent[8] - recent[0]).days == 10
    attention = pd.DataFrame(0.0, index=recent, columns=close.columns)
    attention.iloc[[1, 6], 3] = 3.5  # D: two attention episodes a week apart
    attention.iloc[5:, 4] = 3.5  # E: one news burst stays above 3 for five sessions (7-day counts)
    attention.iloc[0, 5] = 3.5  # F: an earlier, separate news episode plus one ignition
    attention.iloc[5:, 6] = 3.5  # G: the news burst around its single ignition
    assert proposals.find_movers(close, volume, attention) == {"A.US": 2, "D.US": 2, "F.US": 2}
    assert proposals.find_movers(close, volume) == {"A.US": 2}


def test_one_news_burst_is_not_persistence():
    """Single-UTC-day spike through the real a* (7-day rolling counts) stays one episode."""
    recent = SESSIONS[-10:]
    start = recent[0] - timedelta(days=proposals.ATTENTION_HISTORY_DAYS)
    days = [start + timedelta(days=offset) for offset in range((recent[-1] - start).days + 1)]
    rng = np.random.RandomState(0)
    tickers = [f"T{index}.US" for index in range(30)]
    counts = pd.DataFrame(rng.poisson(2, (len(days), len(tickers))).astype(float),
                          index=pd.Index(days, dtype=object), columns=pd.Index(tickers, dtype=object))
    counts.loc[recent[5], "T0.US"] = 200.0
    star = attention_star(counts, recent)
    assert (star["T0.US"] > proposals.MOVER_ATTENTION).sum() >= 2  # one burst, several sessions above 3
    close = pd.DataFrame(100.0, index=pd.Index(SESSIONS, dtype=object), columns=tickers)
    volume = pd.DataFrame(1e6, index=close.index, columns=tickers)
    assert proposals.find_movers(close, volume, star) == {}
    counts.loc[recent[0], "T0.US"] = 200.0  # a second, separate burst a week earlier
    assert proposals.find_movers(close, volume, attention_star(counts, recent)) == {"T0.US": 2}


def test_correlation_clusters_are_connected_components_of_three_or_more():
    rng = np.random.RandomState(1)
    index = pd.Index(SESSIONS, dtype=object)
    n = len(index)
    first, second = rng.normal(0, 0.02, n), rng.normal(0, 0.02, n)
    returns = {
        "A.US": first + rng.normal(0, 0.002, n), "B.US": first + rng.normal(0, 0.002, n),
        "C.US": first + rng.normal(0, 0.002, n), "D.US": second + rng.normal(0, 0.002, n),
        "E.US": second + rng.normal(0, 0.002, n), "F.US": rng.normal(0, 0.02, n),
    }
    close = pd.DataFrame({ticker: 100 * np.cumprod(1 + values) for ticker, values in returns.items()}, index=index)
    assert proposals.correlation_clusters(close, list(returns)) == [["A.US", "B.US", "C.US"]]
    assert proposals.correlation_clusters(close, list(returns), min_size=2) == [
        ["A.US", "B.US", "C.US"], ["D.US", "E.US"]]
    short = close.iloc[-20:]
    assert proposals.correlation_clusters(short, list(returns)) == []  # fewer than 40 observations


def test_merge_matching_and_slugs():
    records = [
        proposals.ProposalRecord(id=1, status="pending", tickers=frozenset({"A", "B", "C", "D"}),
                                 last_seen=AS_OF, hit_count=1, named=True),
        proposals.ProposalRecord(id=2, status="rejected", tickers=frozenset({"A", "B", "C"}),
                                 last_seen=AS_OF, hit_count=1, named=True),
    ]
    assert proposals.best_match({"A", "B", "C"}, records, {"pending"}).id == 1  # Jaccard 0.75
    assert proposals.best_match({"A", "X", "Y"}, records, {"pending"}) is None  # Jaccard 1/6
    assert proposals.best_match({"A", "B", "C"}, records, {"accepted", "rejected"}).id == 2
    assert proposals.normalize_slug(" Grid  Transformers! ") == "grid-transformers"
    assert proposals.normalize_slug("!!!") == ""
