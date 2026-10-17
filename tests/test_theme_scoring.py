"""Pure theme-heat scoring (spec §3.3 and §4 "Scoring") on synthetic frames."""

import math
import random
import time
from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from core.config import settings
from models import CompanyTrendDaily, ThemeDaily, ThemeMemberDaily
from services.themes import scoring
from services.themes.scoring import (
    SCORING_VERSION,
    apply_stage_hold,
    attention_star,
    capped_weights,
    excess_log_return,
    heat_ewma,
    ignition_flags,
    near_high_ratio,
    relative_volume_1d,
    relative_volume_5d,
    robust_z,
    score_themes,
)
from services.themes.types import MemberSpan, ScoringInputs, ThemeMeta


AS_OF = date(2026, 10, 9)  # a Friday
SPAN_START = date(2020, 1, 1)
N_SESSIONS = 260


def _weekdays(end: date, count: int) -> list[date]:
    days, current = [], end
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    return days[::-1]


def _calendar(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _span(slug, ticker, weight=1.0, valid_from=SPAN_START, valid_to=None) -> MemberSpan:
    return MemberSpan(slug, ticker, "core" if weight >= 0.75 else "peripheral", weight, valid_from, valid_to)


def _inputs(close, *, volume=None, benchmark=None, members=(), themes=None, **kwargs) -> ScoringInputs:
    sessions = list(close.index)
    if volume is None:
        volume = pd.DataFrame(1_000_000.0, index=close.index, columns=close.columns)
    if benchmark is None:
        benchmark = pd.Series(100.0, index=close.index)
    if themes is None:
        themes = [ThemeMeta(slug) for slug in sorted({span.theme_slug for span in members})]
    return ScoringInputs(
        as_of=sessions[-1],
        sessions=sessions,
        close=close,
        volume=volume,
        benchmark=benchmark,
        members=list(members),
        themes=list(themes),
        **kwargs,
    )


def _theme_rows(result, slug):
    return [row for row in result.theme_rows if row["theme_slug"] == slug]


def _theme_row(result, slug, day=AS_OF):
    return next(row for row in result.theme_rows if row["theme_slug"] == slug and row["date"] == day)


def _member_rows(result, slug):
    return {row["ticker"]: row for row in result.member_rows if row["theme_slug"] == slug}


def _accelerating_condition(row) -> bool:
    return (
        row["heat_now"] is not None
        and row["heat_trend"] is not None
        and row["heat_now"] > row["heat_trend"] + 5
        and row["score_price"] is not None
        and row["score_price"] > 0.5
        and row["score_breadth"] is not None
        and row["score_breadth"] > 0
    )


def _market(seed=7, *, rocket=False, extra_tickers=(), extra_members=(), extra_themes=(), output_sessions=1):
    """Random-walk universe: 120 stocks, 12 industries of 10, 8 random themes (+ optional rocket).

    The rocket theme's ten members drift down for ~3 months, then rally ~6% a day on
    3.5x volume over the last six sessions (an emerging theme by construction).
    """
    rng = np.random.default_rng(seed)
    sessions = _weekdays(AS_OF, N_SESSIONS)
    n = len(sessions)
    random_tickers = [f"R{i:03d}.US" for i in range(120)]
    rocket_tickers = [f"K{i:02d}.US" for i in range(10)] if rocket else []
    tickers = random_tickers + rocket_tickers + list(extra_tickers)
    returns = rng.normal(0.0003, 0.015, size=(n, len(tickers)))
    volume = rng.lognormal(14.0, 0.2, size=(n, len(tickers)))
    if rocket:
        k = len(random_tickers)
        returns[:, k:k + 10] = rng.normal(0.0, 0.01, size=(n, 10))
        returns[-63:-6, k:k + 10] = -0.01 + rng.normal(0.0, 0.004, size=(57, 10))
        returns[-6:, k:k + 10] = 0.06 + rng.normal(0.0, 0.003, size=(6, 10))
        volume[-6:, k:k + 10] *= 3.5
    close = pd.DataFrame(40.0 * np.exp(np.cumsum(np.log1p(returns), axis=0)), index=sessions, columns=tickers)
    volume = pd.DataFrame(volume, index=sessions, columns=tickers)
    benchmark = pd.Series(500.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.008, n))), index=sessions)
    members, themes = [], []
    for index in range(8):
        slug = f"rand-{index}"
        for position, ticker in enumerate(rng.choice(random_tickers, 10, replace=False)):
            members.append(_span(slug, str(ticker), 1.0 if position < 6 else 0.4))
        themes.append(ThemeMeta(slug))
    if rocket:
        members.extend(_span("rocket", ticker) for ticker in rocket_tickers)
        themes.append(ThemeMeta("rocket", proxy_etfs=("RKT.US",)))
    members.extend(extra_members)
    themes.extend(extra_themes)
    days = _calendar(sessions[0] - timedelta(days=120), AS_OF)
    counts = pd.DataFrame(rng.poisson(1.0, size=(len(days), len(tickers))).astype(float), index=days, columns=tickers)
    totals = pd.Series(2000.0, index=days)
    return ScoringInputs(
        as_of=AS_OF,
        sessions=sessions,
        close=close,
        volume=volume,
        benchmark=benchmark,
        members=members,
        themes=themes,
        industries={ticker: f"ind-{i % 12}" for i, ticker in enumerate(random_tickers)},
        sectors={ticker: f"sector-{i % 5}" for i, ticker in enumerate(tickers)},
        names={ticker: f"Name {ticker}" for ticker in tickers},
        market_caps={ticker: 1e9 + i for i, ticker in enumerate(tickers)},
        universe=frozenset(tickers),
        attention_counts=counts,
        attention_totals=totals,
        output_sessions=output_sessions,
    )


def _assert_plain(value, path):
    """Row values must be plain Python (numpy scalars subclass float, so check exact types)."""
    if value is None or type(value) in (bool, int, str, date):
        return
    if type(value) is float:
        assert math.isfinite(value), path
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _assert_plain(item, f"{path}[{index}]")
        return
    if type(value) is dict:
        for key, item in value.items():
            assert type(key) is str, path
            _assert_plain(item, f"{path}.{key}")
        return
    raise AssertionError(f"{path}: {type(value).__name__} {value!r}")


# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------


def test_row_keys_match_orm_columns_and_values_are_plain_python():
    inputs = _market(rocket=True, output_sessions=5)
    result = score_themes(inputs)

    def columns(model, excluded):
        return {column.name for column in model.__table__.columns} - excluded

    theme_keys = columns(ThemeDaily, {"id", "source_run_id", "theme_id", "scoring_version"}) | {"theme_slug"}
    member_keys = columns(ThemeMemberDaily, {"id", "source_run_id", "theme_id"}) | {"theme_slug"}
    company_keys = columns(CompanyTrendDaily, {"id", "source_run_id"})
    assert result.theme_rows and result.member_rows and result.company_rows
    assert all(set(row) == theme_keys for row in result.theme_rows)
    assert all(set(row) == member_keys for row in result.member_rows)
    assert all(set(row) == company_keys for row in result.company_rows)
    for name, rows in (("theme", result.theme_rows), ("member", result.member_rows), ("company", result.company_rows)):
        for index, row in enumerate(rows):
            _assert_plain(row, f"{name}[{index}]")

    # 9 themes x 5 trailing sessions; members and companies only for as_of.
    assert len(result.theme_rows) == 9 * 5
    assert sorted({row["date"] for row in result.theme_rows}) == inputs.sessions[-5:]
    assert {row["date"] for row in result.member_rows} == {AS_OF}
    assert {row["date"] for row in result.company_rows} == {AS_OF}
    assert result.diagnostics["scoring_version"] == SCORING_VERSION == "theme-heat-v1"
    assert result.diagnostics["rankable_themes"] == 9
    assert result.diagnostics["comparison_industries"] == 12


def test_theme_without_members_is_insufficient_and_does_not_crash():
    inputs = _market(extra_themes=[ThemeMeta("empty", proxy_etfs=("NONE.US",))])
    result = score_themes(inputs)
    row = _theme_row(result, "empty")
    assert row["rankable"] is False and row["stage"] == "insufficient" and row["candidate_stage"] == "insufficient"
    assert row["n_members"] == 0 and row["n_priced"] == 0
    assert row["basket_index"] is None and row["heat"] is None and row["rank_now"] is None
    assert row["leaders"] == [] and row["proxy_etf"] is None
    assert row["flags"]["insufficient_members"] is True
    assert not _member_rows(result, "empty")
    # Nothing to degrade: no member-coverage reason on a basket without priced members.
    assert row["attention_degraded"] is False and row["flags"]["attention_degraded_reasons"] == []
    assert row["score_attention"] is None
    assert result.diagnostics["attention_degraded"] is False
    assert result.diagnostics["attention_day_degraded"] is False


def test_memberless_theme_does_not_mark_the_publication_attention_degraded():
    from services.themes.pipeline import evaluate_theme_trends

    result = score_themes(_market(extra_themes=[ThemeMeta("empty")]))
    active = sorted({row["ticker"] for row in result.member_rows})
    report = evaluate_theme_trends(
        result, target=AS_OF, active_tickers=active, priced_tickers=active, benchmark_priced=True,
        news_published=True,
    )
    assert report.passed, report.errors
    assert report.metrics["attention_degraded"] is False
    assert not any("ttention" in warning for warning in report.warnings)

    # A feed-level problem still degrades every row, members or not.
    inputs = _market(extra_themes=[ThemeMeta("empty")])
    inputs.attention_counts.loc[AS_OF] = np.nan
    inputs.attention_totals.loc[AS_OF] = np.nan
    row = _theme_row(score_themes(inputs), "empty")
    assert row["attention_degraded"] is True
    assert row["flags"]["attention_degraded_reasons"] == ["incomplete_news_days"]


@pytest.mark.parametrize(("unpriced", "industries"), [(2, 12), (3, 11)])
def test_comparison_set_takes_industries_with_eight_priced_members(unpriced, industries):
    inputs = _market()
    ind_0 = sorted(ticker for ticker, industry in inputs.industries.items() if industry == "ind-0")
    assert len(ind_0) == 10
    inputs.close.loc[AS_OF, ind_0[:unpriced]] = np.nan  # 8 or 7 priced members today
    result = score_themes(inputs)
    assert result.diagnostics["comparison_industries"] == industries
    # Industries only shape the reference set; no industry row is emitted.
    assert {row["theme_slug"] for row in result.theme_rows} == {f"rand-{i}" for i in range(8)}


# ---------------------------------------------------------------------------
# Baskets: weights, capping, returns, excess returns
# ---------------------------------------------------------------------------


def test_capped_weights_water_fill_renormalize_and_fallbacks():
    tiered = capped_weights(pd.Series([1.0] * 4 + [0.4] * 4))
    assert tiered.sum() == pytest.approx(1.0)
    assert tiered.iloc[:4].tolist() == pytest.approx([0.15] * 4)
    assert tiered.iloc[4:].tolist() == pytest.approx([0.10] * 4)

    # Capping the dominant name pushes the second over the cap; the next pass pins it too.
    cascade = capped_weights(pd.Series([20.0, 5.0] + [1.0] * 8))
    assert cascade.tolist() == pytest.approx([0.15, 0.15] + [0.7 / 8] * 8)

    # Below 1/cap names the cap is infeasible: equal weights (the flattest distribution).
    assert capped_weights(pd.Series([1.0, 0.4, 0.4])).tolist() == pytest.approx([1 / 3] * 3)

    # Missing, zero and negative weights are excluded, never zero-filled into the mean.
    dirty = capped_weights(pd.Series([1.0] * 8 + [np.nan, 0.0, -1.0]))
    assert dirty.tolist() == pytest.approx([0.125] * 8 + [0.0] * 3)
    assert capped_weights(pd.Series([np.nan, 0.0])).tolist() == [0.0, 0.0]


def test_basket_return_uses_capped_weights_clipped_returns_and_priced_members():
    sessions = _weekdays(AS_OF, 60)
    tickers = [f"M{i}.US" for i in range(8)] + ["GAP.US"]
    close = pd.DataFrame(10.0, index=sessions, columns=tickers)
    last_returns = [0.80, 0.10, 0.0, -0.60, 0.02, 0.0, -0.04, 0.06]
    for ticker, change in zip(tickers, last_returns):
        close.loc[AS_OF, ticker] = 10.0 * (1.0 + change)
    close.loc[sessions[-2], "GAP.US"] = np.nan  # no close at d-1: active but not priced today
    members = [_span("basket", ticker, 1.0 if i < 4 else 0.4) for i, ticker in enumerate(tickers[:8])]
    members.append(_span("basket", "GAP.US", 1.0))
    result = score_themes(_inputs(close, members=members, output_sessions=60))

    row = _theme_row(result, "basket")
    assert row["n_members"] == 9 and row["n_priced"] == 8
    # Core 4 x 0.15 and peripheral 4 x 0.10 after capping; +80% and -60% clip to +-50%.
    expected = 0.15 * (0.5 + 0.10 + 0.0 - 0.5) + 0.10 * (0.02 + 0.0 - 0.04 + 0.06)
    assert row["ret_1d"] == pytest.approx(expected)
    assert row["basket_index"] == pytest.approx(1.0 + expected)
    assert row["x5"] == pytest.approx(math.log(1.0 + expected))  # flat benchmark
    first = _theme_rows(result, "basket")[0]
    assert first["date"] == sessions[0] and first["basket_index"] == 1.0 and first["ret_1d"] is None

    members_out = _member_rows(result, "basket")
    assert [members_out[t]["weight"] for t in tickers[:8]] == pytest.approx([0.15] * 4 + [0.10] * 4)
    assert members_out["GAP.US"]["weight"] == 0.0 and members_out["GAP.US"]["r1"] is None
    assert sum(r["weight"] for r in members_out.values()) == pytest.approx(1.0)
    assert {members_out[t]["tier"] for t in tickers[:4]} == {"core"}
    assert {members_out[t]["tier"] for t in tickers[4:8]} == {"peripheral"}
    assert members_out["M0.US"]["r1"] == pytest.approx(0.80)  # member r1 is the unclipped return


def test_log_excess_returns_against_benchmark():
    sessions = _weekdays(AS_OF, 200)
    t = np.arange(len(sessions), dtype=float)
    path = 50.0 * np.exp(0.01 * t + 0.02 * np.sin(t))
    spy = pd.Series(100.0 * np.exp(0.004 * t + 0.01 * np.cos(t / 3)), index=sessions)
    tickers = [f"S{i}.US" for i in range(6)]
    close = pd.DataFrame({ticker: path for ticker in tickers}, index=sessions)
    members = [_span("excess", ticker) for ticker in tickers]
    result = score_themes(_inputs(close, benchmark=spy, members=members, output_sessions=len(sessions)))

    rows = _theme_rows(result, "excess")
    last = rows[-1]
    for k in (5, 21, 63, 126):
        expected = math.log(path[-1] / path[-1 - k]) - math.log(spy.iloc[-1] / spy.iloc[-1 - k])
        assert last[f"x{k}"] == pytest.approx(expected, abs=1e-10)
    assert last["basket_index"] == pytest.approx(path[-1] / path[0])
    assert rows[125]["x126"] is None and rows[126]["x126"] is not None
    assert rows[4]["x5"] is None and rows[5]["x5"] is not None

    helper = excess_log_return(close, spy, 21)
    assert helper.iloc[-1, 0] == pytest.approx(last["x21"], abs=1e-10)
    member = _member_rows(result, "excess")["S0.US"]
    assert member["x5"] == pytest.approx(last["x5"], abs=1e-10)
    assert member["x21"] == pytest.approx(last["x21"], abs=1e-10)


def test_benchmark_gap_is_reported_where_it_blanks_the_price_score():
    inputs = _market(output_sessions=30)
    sessions = inputs.sessions
    inputs.benchmark.iloc[-22] = np.nan  # SPY has no close 21 sessions before as_of
    result = score_themes(inputs)
    diagnostics = result.diagnostics
    assert diagnostics["benchmark_priced_as_of"] is True
    assert diagnostics["benchmark_missing_sessions"] == 1
    assert diagnostics["benchmark_missing_dates"] == [sessions[-22].isoformat()]
    assert diagnostics["benchmark_missing_lags"] == [21]
    # x_k needs SPY at d and d-k: the gap day itself, 5 and 21 sessions later (63 is past as_of).
    blocked = [sessions[-22].isoformat(), sessions[-17].isoformat(), AS_OF.isoformat()]
    assert diagnostics["benchmark_blocked_dates"] == blocked
    no_heat = sorted({row["date"].isoformat() for row in result.theme_rows if row["rankable"] and row["heat"] is None})
    assert no_heat == blocked
    # Values stay missing rather than filled; the carried heat_now still ranks.
    row = _theme_row(result, "rand-0")
    assert row["x21"] is None and row["score_price"] is None and row["heat"] is None
    assert row["heat_now"] is not None and row["rank_now"] is not None

    clean = score_themes(_market()).diagnostics
    assert clean["benchmark_missing_sessions"] == 0 and clean["benchmark_missing_lags"] == []
    assert clean["benchmark_blocked_dates"] == []


# ---------------------------------------------------------------------------
# Breadth, ignitions, relative volume
# ---------------------------------------------------------------------------


def test_breadth_b50_near_high_and_ten_session_change():
    sessions = _weekdays(AS_OF, N_SESSIONS)
    t = np.arange(len(sessions), dtype=float)
    up = 100.0 * 1.002 ** t
    down = 100.0 * 0.998 ** t
    switch = down.copy()
    switch[-6:] = switch[-7] * 1.12 ** np.arange(1, 7)  # rally over the last six sessions
    columns = {}
    for i in range(4):
        columns[f"UP{i}.US"] = up
    for i in range(3):
        columns[f"DN{i}.US"] = down
    for i in range(3):
        columns[f"SW{i}.US"] = switch
    young = np.full(len(sessions), np.nan)
    young[-30:] = 20.0 * 1.01 ** np.arange(30)  # 30 observations: no MA50, no 126-session high
    columns["NEW.US"] = young
    close = pd.DataFrame(columns, index=sessions)
    members = [_span("breadth", ticker) for ticker in close.columns]
    result = score_themes(_inputs(close, members=members, output_sessions=11))

    row = _theme_row(result, "breadth")
    assert row["n_priced"] == 11
    assert row["b50"] == pytest.approx(0.7)  # 4 up + 3 switched of the 10 with an MA50
    assert row["nh"] == pytest.approx(0.7)
    earlier = _theme_row(result, "breadth", sessions[-11])
    assert earlier["b50"] == pytest.approx(0.4)
    assert row["d_b50"] == pytest.approx(0.3)

    pth = near_high_ratio(close)
    assert pth["UP0.US"].iloc[-1] == pytest.approx(1.0)
    assert pth["DN0.US"].iloc[-1] == pytest.approx(down[-1] / down[-252:].max())
    assert np.isnan(pth["NEW.US"].iloc[-1])
    member = _member_rows(result, "breadth")
    assert member["NEW.US"]["pth"] is None and member["SW0.US"]["pth"] == pytest.approx(1.0)


def test_ma50_rolls_over_the_last_fifty_observations():
    rng = np.random.default_rng(13)
    values = 20.0 * np.exp(np.cumsum(rng.normal(0.0, 0.02, size=(150, 6)), axis=0))
    values[rng.random(values.shape) < 0.1] = np.nan
    values[:70, 4] = np.nan  # listed late
    values[:, 5] = np.nan
    values[[10, 40, 41, 90], 5] = 7.0  # 4 observations: never 50
    flat = np.full((150, 1), 13.37)
    flat[[60, 61, 100], 0] = np.nan
    values = np.hstack([values, flat])
    frame = pd.DataFrame(values)
    # market_breadth.build_price_feature_frame semantics: roll over the rows a ticker has.
    expected = pd.DataFrame({column: frame[column].dropna().rolling(50, min_periods=50).mean() for column in frame})
    expected = expected.reindex(frame.index).to_numpy()
    actual = scoring._rolling_mean_observations(values, 50)
    np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=0, equal_nan=True)
    assert np.isnan(actual[:, 5]).all()
    assert np.nanmax(np.abs(actual[:, 6] - 13.37)) == 0.0  # a flat window means exactly the price


def test_one_missing_session_costs_breadth_one_session_not_fifty():
    inputs = _market(output_sessions=40)
    members = [span.ticker for span in inputs.members if span.theme_slug == "rand-0"]
    gap = inputs.sessions[-29]
    inputs.close.loc[gap, :] = np.nan  # a missing bulk day for every ticker
    result = score_themes(inputs)
    rows = {row["date"]: row for row in _theme_rows(result, "rand-0")}
    after = [day for day in inputs.sessions[-40:] if day > gap]
    # The gap day and the next (no close at d-1) have no priced members, hence no B50 ...
    assert rows[gap]["n_priced"] == 0 and rows[after[0]]["n_priced"] == 0
    assert rows[gap]["b50"] is None and rows[after[0]]["b50"] is None
    # ... but B50 is back as soon as members are priced again, not 50 sessions later. (Heat is
    # still None 5, 21 and 63 sessions on, where an excess-return leg lands on the gap.)
    assert rows[after[1]]["n_priced"] == 10
    assert all(rows[day]["b50"] is not None for day in after[1:])
    row = rows[AS_OF]
    assert row["heat"] is not None
    close = inputs.close[members]
    above = {
        ticker: float(close[ticker].iloc[-1] > close[ticker].dropna().iloc[-50:].mean()) for ticker in members
    }
    weights = {ticker: member["weight"] for ticker, member in _member_rows(result, "rand-0").items()}
    assert row["b50"] == pytest.approx(sum(weights[t] * above[t] for t in members) / sum(weights.values()))


def test_ignition_counts_today_and_distinct_last_five_sessions():
    sessions = _weekdays(AS_OF, 40)
    tickers = ["A.US", "B.US", "C.US", "D.US", "E.US", "F.US", "G.US", "H.US"]
    close = pd.DataFrame(20.0, index=sessions, columns=tickers)
    volume = pd.DataFrame(1e6, index=sessions, columns=tickers)

    def jump(ticker, position, change, volume_multiple):
        close.loc[sessions[position]:, ticker] = 20.0 * (1.0 + change)
        volume.loc[sessions[position], ticker] = 1e6 * volume_multiple

    jump("A.US", -1, 0.06, 3.0)  # ignited today
    jump("B.US", -1, 0.06, 1.5)  # volume too low
    jump("C.US", -1, 0.04, 3.0)  # return too low
    jump("D.US", -4, 0.06, 3.0)  # ignited three sessions ago
    jump("E.US", -8, 0.06, 3.0)  # ignited seven sessions ago (outside the 5-session window)
    jump("F.US", -1, 0.05, 2.0)  # exactly on both thresholds (>=)
    members = [_span("ignite", ticker) for ticker in tickers]
    result = score_themes(_inputs(close, volume=volume, members=members))

    row = _theme_row(result, "ignite")
    assert row["ign"] == 2  # A, F
    assert row["ign5"] == 3  # A, F, D
    flags = ignition_flags(close, volume)
    assert flags.iloc[-1][flags.iloc[-1]].index.tolist() == ["A.US", "F.US"]
    member = _member_rows(result, "ignite")
    assert member["A.US"]["ignited"] is True and member["F.US"]["ignited"] is True
    assert member["D.US"]["ignited"] is False and member["B.US"]["ignited"] is False
    assert member["A.US"]["first_ignition_date"] == AS_OF
    assert member["D.US"]["first_ignition_date"] == sessions[-4]
    assert member["E.US"]["first_ignition_date"] == sessions[-8]
    assert member["B.US"]["first_ignition_date"] is None


def test_relative_volume_and_weighted_median():
    sessions = _weekdays(AS_OF, 80)
    tickers = [f"V{k}.US" for k in range(1, 9)]
    close = pd.DataFrame(10.0, index=sessions, columns=tickers)
    volume = pd.DataFrame(1e6, index=sessions, columns=tickers)
    for k, ticker in enumerate(tickers, start=1):
        volume.loc[sessions[-5]:, ticker] = k * 1e6
    members = [_span("rvol-equal", ticker) for ticker in tickers]
    members += [_span("rvol-tiered", ticker, 0.4 if k <= 4 else 1.0) for k, ticker in enumerate(tickers, start=1)]
    result = score_themes(_inputs(close, volume=volume, members=members))

    rvol5 = relative_volume_5d(volume).iloc[-1]
    assert rvol5.tolist() == pytest.approx([math.log(k) for k in range(1, 9)])
    rvol1 = relative_volume_1d(volume).iloc[-1]
    assert rvol1.tolist() == pytest.approx([20 * k / (16 + 4 * k) for k in range(1, 9)])
    member = _member_rows(result, "rvol-equal")
    assert member["V8.US"]["rvol5"] == pytest.approx(math.log(8))
    # Equal weights: an exact half split averages the two middle values.
    assert _theme_row(result, "rvol-equal")["rvol5_median"] == pytest.approx((math.log(4) + math.log(5)) / 2)
    # Weights 0.10 x4 (low rvol) and 0.15 x4: the cumulative weight crosses 0.5 at ln 5.
    assert _theme_row(result, "rvol-tiered")["rvol5_median"] == pytest.approx(math.log(5))


# ---------------------------------------------------------------------------
# Attention
# ---------------------------------------------------------------------------


def _reference_attention(counts: pd.DataFrame, sessions: list[date]) -> pd.DataFrame:
    """Slow, literal transcription of the spec's a / a* definition."""

    def level(day, ticker):
        window = counts[ticker].reindex([day - timedelta(days=lag) for lag in range(7)])
        return math.nan if window.isna().any() else math.log1p(window.sum())

    raw = {}
    for session in sessions:
        values = {}
        for ticker in counts.columns:
            current = level(session, ticker)
            baseline = [level(session - timedelta(days=lag), ticker) for lag in range(8, 98)]
            baseline = np.array([value for value in baseline if not math.isnan(value)])
            if math.isnan(current) or len(baseline) < 60:
                values[ticker] = math.nan
                continue
            median = float(np.median(baseline))
            mad = float(np.median(np.abs(baseline - median)))
            values[ticker] = (current - median) / max(1.4826 * mad, 0.3)
        raw[session] = values
    frame = pd.DataFrame(raw).T[list(counts.columns)].astype(float)
    return frame.sub(frame.median(axis=1), axis=0).clip(-4, 4)


def test_attention_star_matches_reference_definition():
    rng = np.random.default_rng(3)
    sessions = _weekdays(AS_OF, 12)
    days = _calendar(sessions[0] - timedelta(days=110), AS_OF)
    tickers = [f"N{i}.US" for i in range(6)]
    counts = pd.DataFrame(
        rng.poisson([0.5, 1, 2, 4, 8, 3], size=(len(days), 6)).astype(float), index=days, columns=tickers
    )
    counts.loc[AS_OF - timedelta(days=6):, "N0.US"] += 40  # spike -> clipped to +4
    counts.loc[AS_OF - timedelta(days=40)] = np.nan  # a baseline gap for later sessions
    counts.loc[sessions[3]] = np.nan  # incomplete day inside some sessions' 7-day window
    expected = _reference_attention(counts, sessions)
    actual = attention_star(counts, sessions)
    assert list(actual.index) == sessions and list(actual.columns) == tickers
    pd.testing.assert_frame_equal(actual, expected, check_names=False, atol=1e-12, rtol=0)
    assert actual.loc[AS_OF, "N0.US"] == 4.0
    assert actual.loc[sessions[3]].isna().all()


def test_attention_baseline_needs_sixty_observations_and_floors_mad():
    days = _calendar(AS_OF - timedelta(days=130), AS_OF)
    counts = pd.DataFrame({"FLAT.US": 1.0, "SPIKE.US": 1.0, "Z1.US": 1.0, "Z2.US": 1.0}, index=days)
    counts.loc[AS_OF - timedelta(days=6):, "SPIKE.US"] = 2.0
    # Constant baseline: MAD = 0, so the 0.3 floor sets the scale.
    star = attention_star(counts, [AS_OF])
    assert star.loc[AS_OF, "FLAT.US"] == 0.0
    assert star.loc[AS_OF, "SPIKE.US"] == pytest.approx(min((math.log(15) - math.log(8)) / 0.3, 4.0))

    # NaN days d-60..d-37 leave exactly 60 valid 7-day levels in [d-97, d-8]; one more day leaves 59.
    sixty = counts.copy()
    sixty.loc[AS_OF - timedelta(days=60):AS_OF - timedelta(days=37)] = np.nan
    assert attention_star(sixty, [AS_OF]).notna().all(axis=None)
    fifty_nine = counts.copy()
    fifty_nine.loc[AS_OF - timedelta(days=61):AS_OF - timedelta(days=37)] = np.nan
    assert attention_star(fifty_nine, [AS_OF]).isna().all(axis=None)


def test_attention_healthy_rows_use_full_heat_formula():
    result = score_themes(_market(output_sessions=5))
    for row in result.theme_rows:
        assert row["attention_degraded"] is False
        assert row["flags"]["attention_degraded_reasons"] == []
        assert row["score_attention"] is not None
        expected = 0.40 * row["score_price"] + 0.30 * row["score_breadth"] + 0.15 * row["score_volume"]
        assert row["h_raw"] == pytest.approx(expected + 0.15 * row["score_attention"])
        assert row["heat"] == pytest.approx(100.0 / (1.0 + math.exp(-row["h_raw"])))
    assert result.diagnostics["attention_degraded"] is False


def _assert_degraded(row, reason):
    assert row["attention_degraded"] is True
    assert reason in row["flags"]["attention_degraded_reasons"]
    assert row["score_attention"] is None
    expected = (0.40 * row["score_price"] + 0.30 * row["score_breadth"] + 0.15 * row["score_volume"]) / 0.85
    assert row["h_raw"] == pytest.approx(expected)


@pytest.mark.parametrize("offset", [3, 9])
def test_attention_degraded_by_missing_news_day(offset):
    inputs = _market(output_sessions=15)
    missing = AS_OF - timedelta(days=offset)
    inputs.attention_counts.loc[missing] = np.nan
    inputs.attention_totals.loc[missing] = np.nan
    result = score_themes(inputs)
    for row in _theme_rows(result, "rand-0"):
        # A session's attention window is the 7 days d-6..d, so the gap degrades exactly
        # the sessions dated missing..missing+6, never the ones before it.
        if missing <= row["date"] <= missing + timedelta(days=6):
            _assert_degraded(row, "incomplete_news_days")
        else:
            assert row["attention_degraded"] is False
    inside = (AS_OF - missing).days <= 6
    assert result.diagnostics["attention_day_degraded"] is inside
    assert result.diagnostics["attention_degraded"] is inside
    assert ("incomplete_news_days" in result.diagnostics["attention_day_reasons"]) is inside
    if offset == 9:  # gap on Wed 9/30: 9/30, 10/1, 10/2, 10/5 and 10/6; 10/7 is healthy again
        assert result.diagnostics["attention_degraded_sessions"] == 5


@pytest.mark.parametrize(
    ("multiple", "degraded"),
    [(2.5, False), (2.6, True), (0.4, False), (0.3, True)],
)
def test_attention_degraded_by_total_volume_band(multiple, degraded):
    inputs = _market()
    inputs.attention_totals.loc[AS_OF - timedelta(days=6):] *= multiple  # 7-day total vs 60-day median
    result = score_themes(inputs)
    row = _theme_row(result, "rand-0")
    if degraded:
        _assert_degraded(row, "news_volume_band")
        assert row["flags"]["attention_degraded_reasons"] == ["news_volume_band"]
    else:
        assert row["attention_degraded"] is False
    assert result.diagnostics["attention_day_degraded"] is degraded


@pytest.mark.parametrize(("dropped", "degraded"), [(4, False), (5, True)])
def test_attention_degraded_by_member_coverage(dropped, degraded):
    quiet = [f"Q{i}.US" for i in range(8)]
    inputs = _market(extra_tickers=quiet, extra_members=[_span("quiet", t) for t in quiet],
                     extra_themes=[ThemeMeta("quiet")])
    inputs.attention_counts = inputs.attention_counts.drop(columns=quiet[:dropped])
    result = score_themes(inputs)
    row = _theme_row(result, "quiet")
    assert row["flags"]["attention_coverage"] == pytest.approx((8 - dropped) / 8)
    if degraded:
        _assert_degraded(row, "member_coverage")
        assert row["flags"]["attention_degraded_reasons"] == ["member_coverage"]
    else:
        assert row["attention_degraded"] is False
    assert _theme_row(result, "rand-0")["attention_degraded"] is False
    assert result.diagnostics["attention_day_degraded"] is False
    members = _member_rows(result, "quiet")
    assert members["Q0.US"]["att_z"] is None and members["Q7.US"]["att_z"] is not None


def test_no_attention_data_degrades_every_row():
    inputs = _market()
    inputs.attention_counts = pd.DataFrame()
    inputs.attention_totals = pd.Series(dtype=float)
    result = score_themes(inputs)
    for row in result.theme_rows:
        _assert_degraded(row, "incomplete_news_days")
        assert row["att"] is None
    assert result.diagnostics["attention_day_degraded"] is True


# ---------------------------------------------------------------------------
# Cross-sectional and time-series statistics
# ---------------------------------------------------------------------------


def test_robust_z_clipping_mad_zero_and_reference_mask():
    z = robust_z(pd.Series([1.0, 2.0, 3.0, 4.0, 100.0]))
    assert z.tolist()[:4] == pytest.approx([-2 / 1.4826, -1 / 1.4826, 0.0, 1 / 1.4826])
    assert z.iloc[4] == 3.0  # clipped
    assert robust_z(pd.Series([1.0, 2.0, 3.0, 4.0, -100.0])).iloc[4] == -3.0

    # MAD = 0 falls back to the (population) standard deviation.
    mad_zero = robust_z(pd.Series([5.0, 5.0, 5.0, 5.0, 9.0]))
    assert mad_zero.tolist() == pytest.approx([0.0, 0.0, 0.0, 0.0, 4.0 / 1.6])
    # Both zero: every value scores 0; NaN stays NaN.
    flat = robust_z(pd.Series([7.0, 7.0, np.nan, 7.0]))
    assert flat.iloc[[0, 1, 3]].tolist() == [0.0, 0.0, 0.0] and np.isnan(flat.iloc[2])

    # Values outside the reference set are scored against it.
    masked = robust_z(pd.Series([1.0, 2.0, 3.0, 50.0]), mask=[True, True, True, False], clip=None)
    assert masked.tolist() == pytest.approx([-1 / 1.4826, 0.0, 1 / 1.4826, 48 / 1.4826])


def test_cross_percentile_highest_is_one():
    values = np.array([[1.0, 5.0], [3.0, np.nan], [2.0, 4.0], [10.0, 0.0]])
    reference = np.array([[True, True], [True, True], [True, True], [False, True]])
    pct = scoring._cross_percentile(values, reference)
    assert pct[:, 0].tolist() == pytest.approx([1 / 3, 1.0, 2 / 3, 1.0])
    assert pct[[0, 2, 3], 1].tolist() == pytest.approx([1.0, 2 / 3, 1 / 3]) and np.isnan(pct[1, 1])


@pytest.mark.parametrize("half_life", [5, 20])
def test_heat_ewma_half_life_step_response_and_nan_carry(half_life):
    step = heat_ewma([0.0] + [1.0] * 40, half_life)
    assert step[0] == 0.0
    assert step[half_life] == pytest.approx(0.5)  # half the step after h sessions
    assert step[2 * half_life] == pytest.approx(0.75)

    decay = 0.5 ** (1 / half_life)
    carried = heat_ewma([np.nan, 10.0, np.nan, np.nan, 20.0], half_life)
    assert np.isnan(carried[0])  # seeded by the first available heat
    assert carried[1:4].tolist() == [10.0, 10.0, 10.0]
    assert carried[4] == pytest.approx(decay * 10.0 + (1 - decay) * 20.0)


def test_heat_now_and_trend_are_ewmas_of_heat():
    inputs = _market(output_sessions=N_SESSIONS)
    result = score_themes(inputs)
    rows = _theme_rows(result, "rand-1")
    heat = [math.nan if row["heat"] is None else row["heat"] for row in rows]
    now, trend = heat_ewma(heat, 5), heat_ewma(heat, 20)
    for index, row in enumerate(rows):
        if np.isnan(now[index]):
            assert row["heat_now"] is None and row["heat_trend"] is None
        else:
            assert row["heat_now"] == pytest.approx(now[index])
            assert row["heat_trend"] == pytest.approx(trend[index])
    assert rows[-1]["heat_now"] is not None


def test_ranks_rank_change_streak_and_slug_tie_break():
    twins = [f"R{i:03d}.US" for i in range(0, 100, 10)]
    extra = [_span("twin-a", t) for t in twins] + [_span("twin-b", t) for t in twins]
    inputs = _market(extra_members=extra, extra_themes=[ThemeMeta("twin-b"), ThemeMeta("twin-a")],
                     output_sessions=40)
    result = score_themes(inputs)
    dates = inputs.sessions[-40:]
    by_slug = {}
    for day in dates:
        rows = [row for row in result.theme_rows if row["date"] == day]
        ranked = sorted((row for row in rows if row["rankable"]), key=lambda row: (-row["heat_now"], row["theme_slug"]))
        assert [row["rank_now"] for row in ranked] == list(range(1, len(ranked) + 1))
        trend = sorted((row for row in rows if row["rankable"]), key=lambda row: (-row["heat_trend"], row["theme_slug"]))
        assert [row["rank_trend"] for row in trend] == list(range(1, len(trend) + 1))
        twin_a = next(row for row in rows if row["theme_slug"] == "twin-a")
        twin_b = next(row for row in rows if row["theme_slug"] == "twin-b")
        assert twin_a["heat_now"] == twin_b["heat_now"]
        assert twin_b["rank_now"] == twin_a["rank_now"] + 1  # identical heat: slug order breaks the tie
        for row in rows:
            assert 0.0 < row["pct_now"] <= 1.0 and 0.0 < row["pct_trend"] <= 1.0
            by_slug.setdefault(row["theme_slug"], []).append(row)
    for rows in by_slug.values():
        for previous, row in zip(rows, rows[1:]):
            assert row["rank_change"] == previous["rank_now"] - row["rank_now"]
            expected_streak = previous["streak_top5"] + 1 if row["rank_now"] <= 5 else 0
            assert row["streak_top5"] == expected_streak
    assert result.diagnostics["rankable_themes"] == 10


def test_rank_change_is_none_when_previous_rank_missing():
    inputs = _market(output_sessions=3)
    # Every rand-0 interval closes on as_of (valid_to is exclusive): ranked yesterday, empty today.
    inputs.members = [
        span if span.theme_slug != "rand-0"
        else MemberSpan(span.theme_slug, span.ticker, span.tier, span.weight, SPAN_START, AS_OF)
        for span in inputs.members
    ]
    result = score_themes(inputs)
    rows = _theme_rows(result, "rand-0")
    assert rows[-2]["rank_now"] is not None
    assert rows[-1]["rankable"] is False and rows[-1]["rank_now"] is None and rows[-1]["rank_change"] is None
    assert rows[-1]["streak_top5"] == 0 and rows[-1]["stage"] == "insufficient"


def test_ts_z_requires_sixty_prior_observations():
    result = score_themes(_market(output_sessions=N_SESSIONS))
    rows = _theme_rows(result, "rand-2")
    h_raw = [row["h_raw"] for row in rows]
    first_h = next(index for index, value in enumerate(h_raw) if value is not None)
    assert first_h >= 125  # the 126-observation near-high needs warm-up
    for index, row in enumerate(rows):
        prior = [value for value in h_raw[max(0, index - 252):index] if value is not None]
        if h_raw[index] is None or len(prior) < 60:
            assert row["ts_z"] is None, index
        else:
            assert row["ts_z"] is not None, index
    prior = np.array([value for value in h_raw[-253:-1] if value is not None])
    median = np.median(prior)
    mad = np.median(np.abs(prior - median))
    assert rows[-1]["ts_z"] == pytest.approx((h_raw[-1] - median) / (1.4826 * mad))


def test_cocorr_of_lockstep_members_and_percentile_bounds():
    rng = np.random.default_rng(5)
    lockstep = [f"L{i}.US" for i in range(6)]
    inputs = _market(extra_tickers=lockstep, extra_members=[_span("lockstep", t) for t in lockstep],
                     extra_themes=[ThemeMeta("lockstep")], output_sessions=N_SESSIONS)
    factor = rng.normal(0.0, 0.02, N_SESSIONS)
    for ticker in lockstep:
        returns = factor + rng.normal(0.0, 0.002, N_SESSIONS)
        inputs.close[ticker] = 30.0 * np.exp(np.cumsum(np.log1p(returns)))
    result = score_themes(inputs)
    lock_rows = _theme_rows(result, "lockstep")
    assert lock_rows[-1]["cocorr"] > 0.95
    assert abs(_theme_row(result, "rand-0")["cocorr"]) < 0.2
    # Session 0 has no return; betas need 60 paired returns (index 60) and correlations a
    # full 60-residual window (index 60..119).
    assert lock_rows[118]["cocorr"] is None and lock_rows[119]["cocorr"] is not None
    # Share of the prior <= 252 values at or below today (0 when today is a new low).
    for row in result.theme_rows:
        assert row["cocorr_pct"] is None or 0.0 <= row["cocorr_pct"] <= 1.0
    cocorr = [row["cocorr"] for row in lock_rows]
    prior = [value for value in cocorr[-253:-1] if value is not None]
    assert len(prior) >= 60
    assert lock_rows[-1]["cocorr_pct"] == pytest.approx(sum(v <= cocorr[-1] for v in prior) / len(prior))
    first = next(index for index, value in enumerate(cocorr) if value is not None)
    assert lock_rows[first + 59]["cocorr_pct"] is None and lock_rows[first + 60]["cocorr_pct"] is not None


# ---------------------------------------------------------------------------
# Flags and stages
# ---------------------------------------------------------------------------


def test_apply_stage_hold_three_sessions_and_immediate_emerging():
    candidates = [
        "neutral", "accelerating", "accelerating", "accelerating", "neutral", "fading", "fading",
        "emerging", "neutral", "neutral", "neutral",
    ]
    assert apply_stage_hold(candidates) == [
        "neutral", "neutral", "neutral", "accelerating", "accelerating", "accelerating", "accelerating",
        "emerging", "emerging", "emerging", "neutral",
    ]
    # The series starts at the first candidate.
    assert apply_stage_hold(["fading", "neutral", "neutral"]) == ["fading", "fading", "fading"]
    # Interrupted runs never switch.
    assert apply_stage_hold(["neutral", "leading", "leading", "crowded", "leading", "leading"]) == ["neutral"] * 6


def test_apply_stage_hold_insufficient_blip_resumes_the_previous_view():
    # "insufficient" shows at once, then the hold resumes from the last displayed stage.
    assert apply_stage_hold(["leading", "leading", "insufficient", "neutral", "neutral"]) == [
        "leading", "leading", "insufficient", "leading", "leading",
    ]
    # A one-session blip followed by a one- or two-session candidate never switches.
    assert apply_stage_hold(["neutral"] * 5 + ["insufficient", "crowded", "neutral", "neutral", "neutral"]) == (
        ["neutral"] * 5 + ["insufficient"] + ["neutral"] * 4
    )
    assert apply_stage_hold(["leading"] * 5 + ["insufficient", "fading"] + ["leading"] * 4) == (
        ["leading"] * 5 + ["insufficient"] + ["leading"] * 5
    )
    assert apply_stage_hold(["neutral"] * 3 + ["insufficient", "crowded", "crowded", "neutral"]) == (
        ["neutral"] * 3 + ["insufficient"] + ["neutral"] * 3
    )
    # The gap breaks the run: a candidate needs three consecutive rankable sessions after it.
    assert apply_stage_hold(["neutral"] * 3 + ["crowded", "insufficient", "crowded", "crowded", "crowded"]) == (
        ["neutral"] * 4 + ["insufficient", "neutral", "neutral", "crowded"]
    )
    # Emerging stays immediate after a gap.
    assert apply_stage_hold(["neutral", "insufficient", "emerging"]) == ["neutral", "insufficient", "emerging"]
    # A series that begins unrankable starts from neutral.
    assert apply_stage_hold(["insufficient", "insufficient", "fading", "fading", "fading"]) == [
        "insufficient", "insufficient", "neutral", "neutral", "fading",
    ]


def test_apply_stage_hold_continues_from_pinned_stages():
    # Published stages pin the view; the hold window still reads the candidates.
    pinned = ["crowded", "crowded", None, None]
    assert apply_stage_hold(["leading", "leading", "neutral", "neutral"], fixed=pinned) == ["crowded"] * 4
    assert apply_stage_hold(["leading", "leading", "leading", "neutral"], fixed=pinned) == [
        "crowded", "crowded", "leading", "leading",
    ]
    assert apply_stage_hold(["neutral", "neutral", "emerging"], fixed=["fading", "fading", None]) == [
        "fading", "fading", "emerging",
    ]
    # A pinned "insufficient" with no view before it resumes from neutral.
    assert apply_stage_hold(["neutral", "fading", "fading"], fixed=["insufficient", None, None]) == [
        "insufficient", "neutral", "neutral",
    ]
    # The view starts from the published record: a stage recomputed before the first pin
    # was never displayed, so a record that begins "insufficient" resumes from neutral.
    assert apply_stage_hold(
        ["leading", "leading", "insufficient", "neutral"], fixed=[None, None, "insufficient", None],
    ) == ["leading", "leading", "insufficient", "neutral"]
    assert apply_stage_hold(
        ["crowded"] * 3 + ["insufficient", "insufficient", "fading"], fixed=[None] * 3 + ["insufficient"] * 2 + [None],
    ) == ["crowded"] * 3 + ["insufficient"] * 2 + ["neutral"]
    # A first pin with a real stage still takes over the view.
    assert apply_stage_hold(["fading", "fading", "leading", "neutral"], fixed=[None, None, "leading", None]) == [
        "fading", "fading", "leading", "leading",
    ]


def test_candidate_stage_priority():
    def candidate(rankable=True, crowded=False, leading=False, emerging=False, accelerating=False, fading=False):
        arrays = [np.array([value]) for value in (rankable, crowded, leading, emerging, accelerating, fading)]
        return scoring._candidate_stages(*arrays)[0]

    assert candidate(crowded=True, leading=True, emerging=True, accelerating=True, fading=True) == "crowded"
    assert candidate(leading=True, emerging=True, accelerating=True, fading=True) == "leading"
    assert candidate(emerging=True, accelerating=True, fading=True) == "emerging"
    assert candidate(accelerating=True, fading=True) == "accelerating"
    assert candidate(fading=True) == "fading"
    assert candidate() == "neutral"
    assert candidate(rankable=False, crowded=True, leading=True) == "insufficient"


_CONDITION_SESSIONS = 40


def _conditions(**overrides):
    """``_stage_conditions`` on flat synthetic series where nothing fires unless overridden."""
    n = _CONDITION_SESSIONS
    series = {
        "ts_z": 0.0, "pct_now": 0.5, "pct_trend": 0.5, "pct_x63": 0.5, "x21": 0.0, "b50": 0.5, "d_b50": 0.0,
        "ign5": 0.0, "heat_now": 50.0, "heat_trend": 50.0, "score_price": 0.0, "score_breadth": 0.0,
        "z_att": 0.0, "cocorr_pct": 0.5,
    }
    arrays = {name: np.full(n, value, dtype=float) for name, value in series.items()}
    arrays["rankable"] = np.ones(n, dtype=bool)
    proxy_age_days = overrides.pop("proxy_age_days", None)
    for name, value in overrides.items():
        if callable(value):
            value(arrays[name])
        else:
            arrays[name][:] = value
    if proxy_age_days is not None:
        proxy_age_days = np.full(n, proxy_age_days, dtype=int)
    return scoring._stage_conditions(**arrays, proxy_age_days=proxy_age_days)


def _set(values_by_position):
    """Override callback for ``_conditions``: {position: value} on top of the flat default."""
    def apply(array):
        for position, value in values_by_position.items():
            array[position] = value
    return apply


def _last(**overrides):
    return _conditions(**overrides)["candidates"][-1]


def test_flat_inputs_are_neutral_and_nan_never_fires():
    assert set(_conditions()["candidates"]) == {"neutral"}
    nan_everywhere = {name: np.nan for name in ("ts_z", "pct_now", "pct_trend", "pct_x63", "x21", "b50", "d_b50",
                                                 "heat_now", "heat_trend", "score_price", "score_breadth", "z_att",
                                                 "cocorr_pct")}
    conditions = _conditions(**nan_everywhere)
    assert set(conditions["candidates"]) == {"neutral"}
    assert not conditions["emerge"].any() and not conditions["attention_only"].any()
    assert _conditions(rankable=False)["candidates"] == ["insufficient"] * _CONDITION_SESSIONS


def _leading_trend(qualifying, outside=0):
    """pct_trend 0.75 on ``qualifying`` of the last 20 sessions (+ ``outside`` just before them)."""
    def apply(array):
        array[-qualifying:] = 0.75
        array[-20 - outside:-20] = 0.75
    return apply


def test_leading_needs_fifteen_of_twenty_sessions_in_the_top_quartile_and_b50():
    assert _last(pct_trend=_leading_trend(15), b50=0.6) == "leading"
    assert _last(pct_trend=_leading_trend(14), b50=0.6) == "neutral"
    # A qualifying session 21 sessions ago is outside the window.
    assert _last(pct_trend=_leading_trend(14, outside=1), b50=0.6) == "neutral"
    assert _last(pct_trend=_leading_trend(15), b50=0.59) == "neutral"
    trailing = _conditions(pct_trend=_leading_trend(15), b50=0.6)["leading"]
    assert trailing.tolist() == [False] * (_CONDITION_SESSIONS - 1) + [True]


_ACCELERATING = {"heat_now": 60.0, "heat_trend": 54.9, "score_price": 0.51, "score_breadth": 0.01}


def test_accelerating_thresholds_are_strict():
    assert _last(**_ACCELERATING) == "accelerating"
    assert _last(**{**_ACCELERATING, "heat_trend": 55.0}) == "neutral"  # heat_now = heat_trend + 5
    assert _last(**{**_ACCELERATING, "score_price": 0.5}) == "neutral"
    assert _last(**{**_ACCELERATING, "score_breadth": 0.0}) == "neutral"


@pytest.mark.parametrize(
    ("cocorr_pct", "pct_x63", "crowded"),
    [(0.8, 0.75, True), (0.79, 0.75, False), (0.8, 0.74, False), (np.nan, 1.0, False)],
)
def test_crowded_via_co_movement_and_x63_top_quartile(cocorr_pct, pct_x63, crowded):
    leading = {"pct_trend": _leading_trend(20), "b50": 0.6}
    assert _last(**leading, cocorr_pct=cocorr_pct, pct_x63=pct_x63) == ("crowded" if crowded else "leading")
    assert _last(**_ACCELERATING, cocorr_pct=cocorr_pct, pct_x63=pct_x63) == (
        "crowded" if crowded else "accelerating"
    )
    # Crowding alone (neither leading nor accelerating) is not a stage.
    assert _last(cocorr_pct=cocorr_pct, pct_x63=pct_x63) == "neutral"


@pytest.mark.parametrize(("age_days", "crowded"), [(0, True), (365, True), (366, False), (-1, False)])
def test_crowded_via_proxy_inception_age(age_days, crowded):
    assert _last(**_ACCELERATING, proxy_age_days=age_days) == ("crowded" if crowded else "accelerating")
    assert _last(proxy_age_days=age_days) == "neutral"


def _fading(peak_position=-5, last_heat=65.0, heat_trend=75.0, d_b50=-0.01):
    def heat_now(array):
        array[:] = 66.0
        array[peak_position] = 80.0
        array[-1] = last_heat
    return {"heat_now": heat_now, "heat_trend": heat_trend, "d_b50": d_b50}


def test_fading_needs_all_three_clauses():
    assert _last(**_fading()) == "fading"
    assert _last(**_fading(peak_position=-20)) == "fading"  # the peak is the oldest of the last 20
    assert _last(**_fading(peak_position=-21)) == "neutral"  # ...and now it is outside the window
    assert _last(**_fading(last_heat=65.1)) == "neutral"  # heat_now > 20-session max - 15
    assert _last(**_fading(heat_trend=70.0)) == "neutral"  # heat_now = heat_trend - 5
    assert _last(**_fading(d_b50=0.0)) == "neutral"


_EMERGE = 32  # position of the synthetic emerge session (needs pct_now 21 sessions earlier)


def _emerge_at(position, **changes):
    values = {"ts_z": 2.0, "pct_now_lag": 0.49, "ign5": 3.0, "d_b50": 0.1001, "x21": 0.001, **changes}
    return {
        "ts_z": _set({position: values["ts_z"]}),
        "pct_now": _set({position - 21: values["pct_now_lag"]}),
        "ign5": _set({position: values["ign5"]}),
        "d_b50": _set({position: values["d_b50"]}),
        "x21": _set({position: values["x21"]}),
    }


@pytest.mark.parametrize(
    ("changes", "emerges"),
    [
        ({}, True),
        ({"pct_now_lag": 0.5}, False),  # pct_now(d-21) must be below 0.5
        ({"ts_z": 1.99}, False),
        ({"ign5": 2.0}, False),
        ({"d_b50": 0.10}, False),
        ({"x21": 0.0}, False),
    ],
)
def test_emerge_flag_boundaries(changes, emerges):
    conditions = _conditions(**_emerge_at(_EMERGE, **changes))
    assert bool(conditions["emerge"][_EMERGE]) is emerges
    assert conditions["emerge"].sum() == int(emerges)
    assert (conditions["candidates"][_EMERGE] == "emerging") is emerges


def test_emerging_candidate_lasts_ten_sessions_below_the_leading_percentile():
    candidates = _conditions(**_emerge_at(_EMERGE - 9))["candidates"]
    assert candidates[_EMERGE - 10] == "neutral" and candidates[_EMERGE - 9:_EMERGE + 1] == ["emerging"] * 10
    assert candidates[_EMERGE + 1] == "neutral"  # the emerge session is now 11 sessions back
    # pct_trend >= 0.75 blocks the emerging candidate, not the emerge flag.
    blocked = _conditions(**_emerge_at(_EMERGE), pct_trend=_set({_EMERGE: 0.75}))
    assert blocked["emerge"][_EMERGE] and blocked["candidates"][_EMERGE] == "neutral"


@pytest.mark.parametrize(
    ("z_att", "score_price", "flagged"),
    [(2.0, -0.01, True), (1.99, -0.01, False), (2.0, 0.0, False), (np.nan, -1.0, False)],
)
def test_theme_attention_only_flag(z_att, score_price, flagged):
    conditions = _conditions(z_att=z_att, score_price=score_price)
    assert bool(conditions["attention_only"][-1]) is flagged
    assert conditions["candidates"][-1] == "neutral"  # a flag, never a stage


def test_emerging_first_20_only_on_a_fresh_entry():
    stages = ["neutral"] * 3 + ["emerging"] * 2 + ["neutral"] * 5 + ["emerging"] + ["neutral"] * 21 + ["emerging"]
    changed, first = scoring._stage_transitions(stages, stages)
    assert np.flatnonzero(first).tolist() == [3, 32]  # the re-entry at 10 is within 20 sessions of the first
    assert changed[10] and not first[10]
    assert changed.tolist() == [False] + [stages[i] != stages[i - 1] for i in range(1, len(stages))]
    # A pre-gap "emerging" resumed after a long gap is not a fresh emergence unless the candidate emerges.
    resumed = ["emerging"] + ["insufficient"] * 25 + ["emerging"]
    _, first = scoring._stage_transitions(resumed, resumed[:-1] + ["neutral"])
    assert not first.any()
    _, first = scoring._stage_transitions(resumed, resumed)
    assert first.tolist() == [False] * 26 + [True]


def test_emerging_theme_switches_immediately_and_flags_first_in_twenty():
    inputs = _market(rocket=True, output_sessions=40)
    result = score_themes(inputs)
    rows = _theme_rows(result, "rocket")
    last = rows[-1]
    assert last["emerge"] is True and last["candidate_stage"] == "emerging" and last["stage"] == "emerging"
    assert last["ts_z"] >= 2 and last["ign5"] >= 3 and last["d_b50"] > 0.10 and last["x21"] > 0
    assert rows[-22]["pct_now"] < 0.5
    first = next(index for index, row in enumerate(rows) if row["stage"] == "emerging")
    assert first > 0
    assert rows[first]["candidate_stage"] == "emerging"
    assert rows[first]["stage_prev"] != "emerging" and rows[first]["stage_changed"] is True
    assert rows[first]["emerging_first_20"] is True
    assert all(row["stage"] != "emerging" for row in rows[max(0, first - 20):first])
    assert all(row["emerging_first_20"] is False for row in rows[first + 1:])
    # Switched on the first emerging candidate, without the 3-session hold.
    assert rows[first - 1]["stage"] == rows[first]["stage_prev"] == "neutral"
    assert rows[first - 1]["candidate_stage"] != "emerging"


def test_stage_series_follows_hold_rule_over_the_full_window():
    result = score_themes(_market(rocket=True, output_sessions=N_SESSIONS))
    for slug in ("rocket", "rand-0", "rand-3"):
        rows = _theme_rows(result, slug)
        candidates = [row["candidate_stage"] for row in rows]
        assert [row["stage"] for row in rows] == apply_stage_hold(candidates)
        assert rows[0]["stage_prev"] is None
        for previous, row in zip(rows, rows[1:]):
            assert row["stage_prev"] == previous["stage"]
            assert row["stage_changed"] is (row["stage"] != previous["stage"])
            if row["rankable"]:
                if row["candidate_stage"] == "accelerating":
                    assert _accelerating_condition(row)
                if row["candidate_stage"] in ("neutral", "fading"):
                    assert not _accelerating_condition(row)
                if row["candidate_stage"] == "fading":
                    assert row["heat_now"] < row["heat_trend"] - 5 and row["d_b50"] < 0
            else:
                assert row["candidate_stage"] == "insufficient"


def _rocket_inputs(inception, output_sessions=40):
    inputs = _market(rocket=True, output_sessions=output_sessions)
    inputs.themes = [
        ThemeMeta("rocket", proxy_etfs=("RKT.US",), proxy_inception=inception) if theme.slug == "rocket" else theme
        for theme in inputs.themes
    ]
    return inputs


def _rocket_with_inception(inception, output_sessions=40):
    return _theme_rows(score_themes(_rocket_inputs(inception, output_sessions)), "rocket")


def test_crowded_via_recent_proxy_inception_with_three_session_hold():
    rows = _rocket_with_inception(AS_OF - timedelta(days=100))
    crowded = [index for index, row in enumerate(rows) if row["candidate_stage"] == "crowded"]
    assert crowded and rows[-1]["candidate_stage"] == "crowded"
    for row in rows:
        # Never leading here, so crowded <=> the accelerating condition holds.
        assert (row["candidate_stage"] == "crowded") is _accelerating_condition(row)
    start = crowded[0]
    assert rows[start]["stage"] != "crowded" and rows[start + 1]["stage"] != "crowded"
    assert rows[start + 2]["candidate_stage"] == "crowded" and rows[start + 2]["stage"] == "crowded"
    assert rows[start + 2]["stage_changed"] is True


def test_one_missing_bar_does_not_fast_track_a_stage(monkeypatch):
    monkeypatch.setattr(settings, "THEME_MIN_RANKABLE_MEMBERS", 10)
    inception = AS_OF - timedelta(days=100)
    clean = _rocket_with_inception(inception)
    start = next(index for index, row in enumerate(clean) if row["candidate_stage"] == "crowded")
    inputs = _rocket_inputs(inception)
    inputs.close.loc[clean[start - 2]["date"], "K03.US"] = np.nan
    rows = _theme_rows(score_themes(inputs), "rocket")

    # n_priced needs closes at d and d-1, so one missing bar leaves the 10-member theme
    # unrankable for two sessions, right before its first crowded candidate.
    assert [row["stage"] for row in rows[start - 2:start]] == ["insufficient", "insufficient"]
    view = rows[start - 3]["stage"]
    assert view != "crowded" and rows[start - 3]["rankable"] is True
    assert [row["candidate_stage"] for row in rows[start:start + 3]] == ["crowded"] * 3
    # Back to the pre-gap view first; crowded only after three consecutive rankable sessions.
    assert rows[start]["stage"] == rows[start + 1]["stage"] == view
    assert rows[start]["stage_prev"] == "insufficient" and rows[start + 1]["stage_changed"] is False
    assert rows[start + 2]["stage"] == "crowded" and rows[start + 2]["stage_prev"] == view
    assert not any(row["stage"] == "crowded" and row["stage_prev"] == "insufficient" for row in rows)


def _published(rows):
    keys = ("theme_slug", "date", "stage", "candidate_stage", "rank_now", "streak_top5")
    return [{key: row[key] for key in keys} for row in rows]


def test_published_history_pins_previous_sessions():
    inputs = _market(rocket=True, output_sessions=1)
    sessions = inputs.sessions
    baseline = score_themes(inputs)
    row = _theme_row(baseline, "rand-0")
    assert row["candidate_stage"] not in ("leading", "emerging")
    assert baseline.diagnostics["published_sessions_pinned"] == 0

    history = [
        {"theme_slug": "rand-0", "date": day, "stage": "leading", "candidate_stage": "leading",
         "rank_now": 1, "streak_top5": 7}
        for day in sessions[-26:-1]
    ]
    pinned = score_themes(inputs, published=history)
    assert pinned.diagnostics["published_sessions_pinned"] == 25
    row = _theme_row(pinned, "rand-0")
    # The published "leading" view holds: today's candidate has not held three sessions.
    assert row["stage_prev"] == "leading" and row["stage"] == "leading" and row["stage_changed"] is False
    assert row["rank_change"] == 1 - row["rank_now"]
    assert row["streak_top5"] == (8 if row["rank_now"] <= 5 else 0)
    # Only rand-0 was pinned, and only its "previous session" values moved.
    unpinned = {key for key in row if row[key] != _theme_row(baseline, "rand-0")[key]}
    assert unpinned <= {"stage", "stage_prev", "stage_changed", "rank_change", "streak_top5"}
    for slug in ("rand-1", "rocket"):
        assert _theme_row(pinned, slug) == _theme_row(baseline, slug)

    # Stage-only rows keep the recomputed ranks; rows dated inside the output window are ignored.
    stage_only = [{"theme_slug": "rand-0", "date": day.isoformat(), "stage": "fading"} for day in sessions[-3:-1]]
    row = _theme_row(score_themes(inputs, published=stage_only), "rand-0")
    assert row["stage_prev"] == "fading" and row["rank_change"] == _theme_row(baseline, "rand-0")["rank_change"]
    rescored = [{"theme_slug": "rand-0", "date": AS_OF, "stage": "crowded"}, {"theme_slug": "nope", "date": AS_OF}]
    assert score_themes(inputs, published=rescored).theme_rows == baseline.theme_rows


@pytest.mark.parametrize("seed", [0, 2, 3, 9])
def test_new_theme_with_unrankable_published_record_starts_from_neutral(seed):
    # Members arrive on the previous session; the 30 published sessions before today were
    # "insufficient". The PIT fallback makes the older window rankable on today's members,
    # but those recomputed (never displayed) stages must not surface as today's stage.
    fresh = [f"F{i}.US" for i in range(10)]
    sessions = _weekdays(AS_OF, N_SESSIONS)
    inputs = _market(seed=seed, extra_tickers=fresh, extra_themes=[ThemeMeta("fresh")],
                     extra_members=[_span("fresh", ticker, valid_from=sessions[-2]) for ticker in fresh])
    history = [
        {"theme_slug": "fresh", "date": day, "stage": "insufficient", "candidate_stage": "insufficient",
         "rank_now": None, "streak_top5": 0}
        for day in sessions[-31:-1]
    ]
    result = score_themes(inputs, published=history)
    row = _theme_row(result, "fresh")
    assert row["pit"] is True and row["rankable"] is True and row["stage_prev"] == "insufficient"
    candidate = row["candidate_stage"]
    assert row["stage"] == (candidate if candidate in ("neutral", "emerging") else "neutral")
    assert row["stage_changed"] is True
    # The session just before the pins was recomputed on today's members (non-PIT) to a
    # stage that differs from the one shown, so carrying it over would show a stale stage.
    unpinned = _theme_rows(score_themes(replace(inputs, output_sessions=32)), "fresh")[0]
    assert unpinned["date"] == sessions[-32] and unpinned["pit"] is False
    assert unpinned["stage"] not in (row["stage"], "insufficient")


def test_consecutive_runs_chain_through_the_published_history():
    inputs = _market(rocket=True)
    sessions = inputs.sessions
    yesterday = score_themes(replace(inputs, as_of=sessions[-2], sessions=sessions[:-1], output_sessions=30))
    today = score_themes(replace(inputs, sessions=sessions[1:], output_sessions=1),
                         published=_published(yesterday.theme_rows))
    assert today.diagnostics["published_sessions_pinned"] == 30
    for slug in ("rocket", "rand-0", "rand-5"):
        previous = _theme_rows(yesterday, slug)
        row = _theme_row(today, slug)
        assert row["stage_prev"] == previous[-1]["stage"]
        expected = apply_stage_hold(
            [r["candidate_stage"] for r in previous] + [row["candidate_stage"]],
            fixed=[r["stage"] for r in previous] + [None],
        )
        assert row["stage"] == expected[-1]
        assert row["stage_changed"] is (row["stage"] != previous[-1]["stage"])
        assert row["emerging_first_20"] is (
            row["stage_changed"] and row["stage"] == row["candidate_stage"] == "emerging"
            and all(r["stage"] != "emerging" for r in previous[-20:])
        )
        if row["rank_now"] is not None and previous[-1]["rank_now"] is not None:
            assert row["rank_change"] == previous[-1]["rank_now"] - row["rank_now"]
        assert row["streak_top5"] == (
            previous[-1]["streak_top5"] + 1 if row["rank_now"] is not None and row["rank_now"] <= 5 else 0
        )
    assert _theme_row(today, "rocket")["stage"] == "emerging"


def test_theme_attention_only_flag_end_to_end():
    hype = [f"H{i}.US" for i in range(8)]
    inputs = _market(extra_tickers=hype, extra_members=[_span("hype", t) for t in hype],
                     extra_themes=[ThemeMeta("hype")])
    for position in range(-21, 0):  # 2% a day below the benchmark for a month
        for ticker in hype:
            inputs.close.iloc[position, inputs.close.columns.get_loc(ticker)] = (
                inputs.close[ticker].iloc[position - 1] * 0.98
            )
    inputs.attention_counts.loc[AS_OF - timedelta(days=6):, hype] = 40.0
    result = score_themes(inputs)
    row = _theme_row(result, "hype")
    assert row["score_price"] < 0 and row["att"] > 2
    assert row["attention_only"] is True and row["stage"] != "insufficient"
    for other in result.theme_rows:
        if other["attention_only"]:
            assert other["score_price"] < 0


@pytest.mark.parametrize(
    ("inception", "crowded_at_as_of"),
    [
        (AS_OF, True),
        (AS_OF - timedelta(days=365), True),
        (AS_OF - timedelta(days=366), False),
        (AS_OF - timedelta(days=800), False),
        (AS_OF + timedelta(days=10), False),
        (None, False),
    ],
)
def test_crowded_proxy_inception_window(inception, crowded_at_as_of):
    last = _rocket_with_inception(inception, output_sessions=1)[-1]
    assert _accelerating_condition(last)
    assert (last["candidate_stage"] == "crowded") is crowded_at_as_of


def test_insufficient_members_reads_setting_at_call_time(monkeypatch):
    small = [f"R{i:03d}.US" for i in range(100, 105)]
    patchy = [f"R{i:03d}.US" for i in range(105, 112)]
    inputs = _market(
        extra_members=[_span("small", t) for t in small] + [_span("patchy", t) for t in patchy],
        extra_themes=[ThemeMeta("small"), ThemeMeta("patchy")],
    )
    inputs.close.loc[AS_OF, patchy[:2]] = np.nan  # 7 members, 5 priced today
    result = score_themes(inputs)
    for slug, members, priced in (("small", 5, 5), ("patchy", 7, 5)):
        row = _theme_row(result, slug)
        assert (row["n_members"], row["n_priced"]) == (members, priced)
        assert row["rankable"] is False and row["stage"] == "insufficient"
        assert row["candidate_stage"] == "insufficient"
        assert row["rank_now"] is None and row["rank_trend"] is None and row["rank_change"] is None
        assert row["flags"]["insufficient_members"] is True
        assert row["heat"] is not None  # still scored against the comparison set, just not ranked

    monkeypatch.setattr(settings, "THEME_MIN_RANKABLE_MEMBERS", 5)
    relaxed = score_themes(inputs)
    assert relaxed.diagnostics["min_rankable_members"] == 5
    for slug in ("small", "patchy"):
        row = _theme_row(relaxed, slug)
        assert row["rankable"] is True and row["rank_now"] is not None
        assert row["flags"]["insufficient_members"] is False


def test_point_in_time_flag_and_as_of_membership_fallback():
    sessions = _weekdays(AS_OF, N_SESSIONS)
    current = [f"R{i:03d}.US" for i in range(20, 28)]
    extra = [_span("late", t, valid_from=sessions[200]) for t in current]
    extra.append(_span("late", "R030.US", valid_from=sessions[100], valid_to=sessions[150]))
    result = score_themes(_market(extra_members=extra, extra_themes=[ThemeMeta("late")], output_sessions=N_SESSIONS))
    rows = _theme_rows(result, "late")
    for index, row in enumerate(rows):
        if index < 100:  # no span has started yet: today's members stand in
            assert row["pit"] is False and row["n_members"] == 8
        elif index < 150:
            assert row["pit"] is True and row["n_members"] == 1 and row["stage"] == "insufficient"
        elif index < 200:
            assert row["pit"] is True and row["n_members"] == 0
        else:
            assert row["pit"] is True and row["n_members"] == 8
    assert all(row["pit"] is True for row in _theme_rows(result, "rand-0"))
    assert set(_member_rows(result, "late")) == set(current)  # closed intervals are not members today


def test_proxy_prefers_first_etf_with_computable_excess():
    inputs = _market(extra_themes=[ThemeMeta("proxied", proxy_etfs=("NEW.US", "OLD.US", "GONE.US"))],
                     extra_members=[_span("proxied", f"R{i:03d}.US") for i in range(8)], output_sessions=15)
    sessions = inputs.sessions
    t = np.arange(len(sessions), dtype=float)
    new = np.full(len(sessions), np.nan)
    new[-30:] = 25.0 * np.exp(0.01 * np.arange(30))
    inputs.proxy_close = pd.DataFrame({"NEW.US": new, "OLD.US": 80.0 * np.exp(0.002 * t)}, index=sessions)
    result = score_themes(inputs)
    rows = _theme_rows(result, "proxied")
    spy = inputs.benchmark
    for row in rows:
        position = sessions.index(row["date"])
        if position >= len(sessions) - 9:  # NEW has 21 prior sessions from here on
            expected_etf, prices = "NEW.US", new
        else:
            expected_etf, prices = "OLD.US", 80.0 * np.exp(0.002 * t)
        assert row["proxy_etf"] == expected_etf
        expected = math.log(prices[position] / prices[position - 21]) - math.log(
            spy.iloc[position] / spy.iloc[position - 21]
        )
        assert row["proxy_x21"] == pytest.approx(expected)

    # Proxy prices fall back to the main close frame; no prices at all -> None.
    inputs.proxy_close = pd.DataFrame()
    inputs.close["OLD.US"] = 80.0 * np.exp(0.002 * t)
    assert _theme_row(score_themes(inputs), "proxied")["proxy_etf"] == "OLD.US"
    inputs.close = inputs.close.drop(columns=["OLD.US"])
    row = _theme_row(score_themes(inputs), "proxied")
    assert row["proxy_etf"] is None and row["proxy_x21"] is None


# ---------------------------------------------------------------------------
# Members and companies
# ---------------------------------------------------------------------------


def _pct_max(values: dict) -> dict:
    present = {key: value for key, value in values.items() if value is not None}
    return {key: sum(other <= value for other in present.values()) / len(present) for key, value in present.items()}


def _expected_leader_scores(members: dict) -> dict:
    components = [("x21", 0.4), ("pth", 0.3), ("att_z", 0.3)]
    pct = {name: _pct_max({t: row[name] for t, row in members.items()}) for name, _ in components}
    scores = {}
    for ticker in members:
        parts = [(weight, pct[name][ticker]) for name, weight in components if ticker in pct[name]]
        scores[ticker] = sum(w * p for w, p in parts) / sum(w for w, _ in parts) if parts else None
    return scores


def test_member_rows_leader_scores_ranks_and_sparkline():
    inputs = _market(output_sessions=3)
    result = score_themes(inputs)
    members = _member_rows(result, "rand-0")
    assert len(members) == 10
    expected = _expected_leader_scores(members)
    for ticker, row in members.items():
        assert row["leader_score"] == pytest.approx(expected[ticker])
    ordered = sorted(members, key=lambda t: (-members[t]["leader_score"], t))
    assert [members[t]["leader_rank"] for t in ordered] == list(range(1, 11))
    assert [t for t in ordered if members[t]["is_leader"]] == ordered[:3]
    assert _theme_row(result, "rand-0")["leaders"] == ordered[:3]
    assert [row["ticker"] for row in result.member_rows if row["theme_slug"] == "rand-0"] == ordered

    row = members[ordered[0]]
    closes = inputs.close[row["ticker"]].iloc[-60:].to_numpy()
    assert len(row["sparkline"]) == 60 and row["sparkline"][0] == 1.0
    assert row["sparkline"] == pytest.approx((closes / closes[0]).tolist(), abs=1e-6)
    assert row["close"] == pytest.approx(closes[-1])
    assert sum(r["weight"] for r in members.values()) == pytest.approx(1.0)
    spans = {span.ticker: span for span in inputs.members if span.theme_slug == "rand-0"}
    assert all(r["tier"] == spans[t].tier for t, r in members.items())


def test_leader_scores_are_nan_safe():
    patchy = [f"P{i}.US" for i in range(8)]
    inputs = _market(extra_tickers=patchy, extra_members=[_span("patchy", t) for t in patchy],
                     extra_themes=[ThemeMeta("patchy")])
    inputs.attention_counts = inputs.attention_counts.drop(columns=["P0.US"])  # no a*
    inputs.close.loc[: inputs.sessions[-101], "P1.US"] = np.nan  # 100 sessions: no 252-day high
    inputs.close.loc[inputs.sessions[-40]: inputs.sessions[-20], "P2.US"] = np.nan  # gaps in the sparkline
    inputs.close.loc[AS_OF, "P3.US"] = np.nan  # unpriced today: a* only
    inputs.close.loc[inputs.sessions[-60]: inputs.sessions[-58], "P4.US"] = np.nan  # sparkline starts late
    result = score_themes(inputs)
    members = _member_rows(result, "patchy")
    assert members["P0.US"]["att_z"] is None and members["P1.US"]["pth"] is None
    assert members["P3.US"]["x21"] is None and members["P3.US"]["att_z"] is not None
    assert members["P3.US"]["weight"] == 0.0 and members["P3.US"]["close"] is None
    expected = _expected_leader_scores(members)
    for ticker, row in members.items():
        assert row["leader_score"] is not None
        assert row["leader_score"] == pytest.approx(expected[ticker])
    assert sorted(row["leader_rank"] for row in members.values()) == list(range(1, 9))
    spark = members["P2.US"]["sparkline"]
    assert len(spark) == 60 and spark[0] == 1.0 and spark[20:41] == [None] * 21
    spark = members["P4.US"]["sparkline"]
    first = inputs.close["P4.US"].iloc[-57]
    assert spark[:3] == [None] * 3 and spark[3] == 1.0
    assert spark[-1] == pytest.approx(inputs.close["P4.US"].iloc[-1] / first, abs=1e-6)
    assert members["P3.US"]["sparkline"][-1] is None


def test_company_rows_eligibility_score_and_flags():
    specials = ["PENNY.US", "THIN.US", "BUZZ.US", "POP.US"]
    theme_members = [_span("rand-extra", "BUZZ.US")] + [_span("rand-extra", f"R{i:03d}.US") for i in range(5)]
    inputs = _market(extra_tickers=specials, extra_members=theme_members,
                     extra_themes=[ThemeMeta("rand-extra")])
    sessions, close, volume = inputs.sessions, inputs.close, inputs.volume
    spy = inputs.benchmark
    close["PENNY.US"] = close["PENNY.US"] / close["PENNY.US"].iloc[-1] * 2.0  # $2 stock
    volume["THIN.US"] = 1_000.0  # ~$40k a day
    for position in range(-5, 0):  # 0.5% a day under the benchmark on 4x volume
        close.iloc[position, close.columns.get_loc("BUZZ.US")] = (
            close["BUZZ.US"].iloc[position - 1] * spy.iloc[position] / spy.iloc[position - 1] * 0.995
        )
    volume.loc[sessions[-5]:, "BUZZ.US"] *= 4.0
    inputs.attention_counts.loc[AS_OF - timedelta(days=6):, "BUZZ.US"] = 30.0
    close.loc[AS_OF, "POP.US"] = close["POP.US"].iloc[-2] * 1.08  # ignites today
    volume.loc[AS_OF, "POP.US"] = volume["POP.US"].iloc[-21:-1].mean() * 5.0
    result = score_themes(inputs)
    rows = {row["ticker"]: row for row in result.company_rows}

    # Recompute CT independently from the public helpers.
    x5 = excess_log_return(close, spy, 5).iloc[-1]
    rvol5 = relative_volume_5d(volume).iloc[-1]
    att = attention_star(inputs.attention_counts, sessions).iloc[-1].reindex(close.columns)
    pth = near_high_ratio(close).iloc[-1]
    dollar = (close * volume).iloc[-20:]
    eligible = (close.iloc[-1] >= 3.0) & (dollar.median() >= 5e6) & (dollar.notna().sum() >= 10)
    assert not eligible["PENNY.US"] and not eligible["THIN.US"]
    assert result.diagnostics["companies_eligible"] == int(eligible.sum()) == len(close.columns) - 2
    sectors = pd.Series(inputs.sectors).reindex(close.columns)
    sector_median = x5[eligible].groupby(sectors[eligible]).median()
    x5_sector = x5 - sectors.map(sector_median)
    numerator = pd.Series(0.0, index=close.columns)
    denominator = pd.Series(0.0, index=close.columns)
    for values, weight in ((x5_sector, 0.35), (rvol5, 0.25), (att, 0.20), (pth, 0.20)):
        z = robust_z(values, mask=eligible)
        numerator += z.fillna(0.0) * weight
        denominator += z.notna() * weight
    expected = (numerator / denominator)[eligible].sort_values(ascending=False, kind="stable")

    assert len(result.company_rows) == 50
    assert [row["rank"] for row in result.company_rows] == list(range(1, 51))
    scores = [row["score"] for row in result.company_rows]
    assert scores == sorted(scores, reverse=True)
    assert set(rows) == set(expected.index[:50])
    for ticker, row in rows.items():
        assert row["score"] == pytest.approx(expected[ticker])
        assert row["x5_sector"] == pytest.approx(x5_sector[ticker])
        assert row["name"] == f"Name {ticker}" and row["sector"] == inputs.sectors[ticker]
        assert row["market_cap"] == inputs.market_caps[ticker]
    assert "PENNY.US" not in rows and "THIN.US" not in rows

    buzz = rows["BUZZ.US"]
    assert buzz["att_z"] == 4.0 and buzz["x5"] < 0
    assert buzz["flags"] == ["attention_only"]
    assert buzz["theme_slugs"] == ["rand-extra"]
    assert "ignited" in rows["POP.US"]["flags"]
    member_of = {row["ticker"] for row in result.member_rows if row["theme_slug"] == "rand-0"}
    for ticker in member_of & set(rows):
        assert "rand-0" in rows[ticker]["theme_slugs"]


# ---------------------------------------------------------------------------
# Determinism and performance
# ---------------------------------------------------------------------------


def test_scoring_is_deterministic_and_order_independent():
    inputs = _market(rocket=True, output_sessions=5)
    first = score_themes(inputs)
    again = score_themes(inputs)
    assert first.theme_rows == again.theme_rows
    assert first.member_rows == again.member_rows
    assert first.company_rows == again.company_rows

    shuffle = random.Random(1)
    columns = list(inputs.close.columns)
    shuffle.shuffle(columns)
    count_columns = list(inputs.attention_counts.columns)
    shuffle.shuffle(count_columns)
    members, themes = list(inputs.members), list(inputs.themes)
    shuffle.shuffle(members)
    shuffle.shuffle(themes)
    industries = list(inputs.industries.items())
    shuffle.shuffle(industries)
    shuffled = ScoringInputs(
        as_of=inputs.as_of,
        sessions=list(inputs.sessions),
        close=inputs.close[columns],
        volume=inputs.volume[columns[::-1]],
        benchmark=inputs.benchmark,
        members=members,
        themes=themes,
        industries=dict(industries),
        sectors=inputs.sectors,
        names=inputs.names,
        market_caps=inputs.market_caps,
        universe=inputs.universe,
        attention_counts=inputs.attention_counts[count_columns],
        attention_totals=inputs.attention_totals,
        output_sessions=5,
    )
    other = score_themes(shuffled)
    assert other.theme_rows == first.theme_rows
    assert other.member_rows == first.member_rows
    assert other.company_rows == first.company_rows


def test_inputs_are_not_mutated_and_timestamp_indexes_are_accepted():
    inputs = _market(output_sessions=2)
    close_before = inputs.close.copy()
    baseline = score_themes(inputs)
    pd.testing.assert_frame_equal(inputs.close, close_before)

    stamped = _market(output_sessions=2)
    stamped.close.index = pd.to_datetime(stamped.close.index)
    stamped.volume.index = pd.to_datetime(stamped.volume.index)
    stamped.benchmark.index = pd.to_datetime(stamped.benchmark.index)
    stamped.attention_counts.index = pd.to_datetime(stamped.attention_counts.index)
    stamped.attention_totals.index = pd.to_datetime(stamped.attention_totals.index)
    stamped.sessions = [pd.Timestamp(day) for day in stamped.sessions]
    result = score_themes(stamped)
    assert result.theme_rows == baseline.theme_rows
    assert all(type(row["date"]) is date for row in result.theme_rows)


def test_performance_smoke_3000_tickers_260_sessions_20_themes():
    rng = np.random.default_rng(11)
    sessions = _weekdays(AS_OF, N_SESSIONS)
    tickers = [f"T{i:04d}.US" for i in range(3000)]
    returns = rng.normal(0.0005, 0.02, size=(len(sessions), len(tickers)))
    close = pd.DataFrame(50.0 * np.exp(np.cumsum(returns, axis=0)), index=sessions, columns=tickers)
    close.iloc[rng.random(close.shape) < 0.01] = np.nan  # sprinkle missing bars
    volume = pd.DataFrame(rng.lognormal(13.0, 0.4, size=close.shape), index=sessions, columns=tickers)
    benchmark = pd.Series(400.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, len(sessions)))), index=sessions)
    members, themes = [], []
    for index in range(20):
        slug = f"theme-{index:02d}"
        for position, pick in enumerate(rng.choice(len(tickers), size=40, replace=False)):
            members.append(_span(slug, tickers[pick], 1.0 if position < 20 else 0.4))
        themes.append(ThemeMeta(slug, proxy_etfs=(f"P{index:02d}.US",), proxy_inception=date(2026, 1, 1)))
    days = _calendar(sessions[0] - timedelta(days=110), AS_OF)
    counts = pd.DataFrame(rng.poisson(0.5, size=(len(days), len(tickers))).astype(float), index=days, columns=tickers)
    proxy = pd.DataFrame(
        100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.015, (len(sessions), 20)), axis=0)),
        index=sessions,
        columns=[theme.proxy_etfs[0] for theme in themes],
    )
    inputs = ScoringInputs(
        as_of=AS_OF,
        sessions=sessions,
        close=close,
        volume=volume,
        benchmark=benchmark,
        members=members,
        themes=themes,
        industries={ticker: f"ind-{i % 150}" for i, ticker in enumerate(tickers)},
        sectors={ticker: f"sector-{i % 11}" for i, ticker in enumerate(tickers)},
        universe=frozenset(tickers),
        attention_counts=counts,
        attention_totals=pd.Series(1900.0, index=days),
        proxy_close=proxy,
        output_sessions=1,
    )
    started = time.perf_counter()
    result = score_themes(inputs)
    elapsed = time.perf_counter() - started
    assert elapsed < 10.0, f"score_themes took {elapsed:.1f}s"
    assert len(result.theme_rows) == 20 and len(result.company_rows) == 50
    assert result.diagnostics["rankable_themes"] == 20
    assert all(row["heat"] is not None for row in result.theme_rows)
