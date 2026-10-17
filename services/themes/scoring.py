"""Pure theme-heat scoring (no DB, no I/O). Spec: docs/themes/IMPLEMENTATION_SPEC.md §Scoring.

Price inputs are wide frames: rows are ascending sessions, columns are canonical
tickers. Per-ticker features are computed once over the whole window; every basket
(a theme, or an industry in the comparison set) is then a small column subset, so
the cost is a few rolling passes over the full frame plus cheap per-basket algebra.
"""

from __future__ import annotations

import math
import warnings
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from core.config import settings
from services.themes.types import STAGES, MemberSpan, ScoringInputs, ScoringResult, ThemeMeta


SCORING_VERSION = "theme-heat-v1"

# Basket construction.
MAX_MEMBER_WEIGHT = 0.15
WEIGHT_CAP_PASSES = 10
RETURN_CLIP = 0.5
EXCESS_WINDOWS = (5, 21, 63, 126)
INDUSTRY_MIN_MEMBERS = 8

# Breadth, ignition and volume.
MA_WINDOW = 50
HIGH_WINDOW = 252
HIGH_MIN_OBS = 126
NEAR_HIGH = 0.98
D_B50_LAG = 10
IGNITION_RETURN = 0.05
IGNITION_RVOL = 2.0
IGNITION_RECENT_SESSIONS = 5
# The spec gives no minimum observation counts for the volume windows; half a
# window tolerates single-day halts and missing bars without inventing data.
RVOL1_WINDOW = 20
RVOL1_MIN_OBS = 10
RVOL5_SHORT_WINDOW = 5
RVOL5_SHORT_MIN_OBS = 3
RVOL5_LONG_WINDOW = 60
RVOL5_LONG_MIN_OBS = 30

# Attention (UTC calendar days).
ATT_WINDOW_DAYS = 7
ATT_BASELINE_FIRST_LAG = 97  # baseline covers calendar days d-97 .. d-8
ATT_BASELINE_LAST_LAG = 8
ATT_BASELINE_MIN_OBS = 60
ATT_MAD_FLOOR = 0.3
ATT_CLIP = 4.0
ATT_BREADTH_THRESHOLD = 2.0
ATT_TOTAL_BAND = (0.4, 2.5)
ATT_TOTAL_BASELINE_DAYS = 60
ATT_TOTAL_BASELINE_MIN_OBS = 30
ATT_MIN_MEMBER_COVERAGE = 0.5

# Composite.
Z_CLIP = 3.0
W_P = (("x21", 0.5), ("x63", 0.3), ("x5", 0.2))
W_B = (("b50", 0.4), ("nh", 0.3), ("d_b50", 0.3))
W_H = (("P", 0.40), ("B", 0.30), ("V", 0.15), ("A", 0.15))
W_A = (("att", 0.6), ("att_breadth", 0.4))
HALF_LIFE_NOW = 5
HALF_LIFE_TREND = 20
TS_Z_WINDOW = 252
TS_Z_MIN_OBS = 60

# Co-movement (crowding).
BETA_WINDOW = 120
BETA_MIN_OBS = 60
COCORR_WINDOW = 60
COCORR_MIN_MEMBERS = 3
COCORR_PCT_WINDOW = 252
COCORR_PCT_MIN_OBS = 60

# Flags and lifecycle.
EMERGE_TS_Z = 2.0
EMERGE_PCT_LAG = 21
EMERGE_PCT_MAX = 0.5
EMERGE_MIN_IGN5 = 3
EMERGE_MIN_D_B50 = 0.10
EMERGE_LOOKBACK = 10
ATTENTION_ONLY_Z = 2.0
CROWDED_COCORR_PCT = 0.8
CROWDED_X63_PCT = 0.75
PROXY_INCEPTION_DAYS = 365
LEADING_WINDOW = 20
LEADING_MIN_SESSIONS = 15
LEADING_PCT = 0.75
LEADING_MIN_B50 = 0.6
EMERGING_MAX_PCT_TREND = 0.75
STAGE_GAP = 5.0
ACCEL_MIN_P = 0.5
FADING_WINDOW = 20
FADING_DROP = 15.0
STAGE_HOLD = 3
EMERGING_REPEAT_SESSIONS = 20

# Members and companies.
LEADER_COUNT = 3
LEADER_WEIGHTS = (("x21", 0.4), ("pth", 0.3), ("att", 0.3))
FIRST_IGNITION_SESSIONS = 63
SPARKLINE_SESSIONS = 60
COMPANY_MIN_CLOSE = 3.0
COMPANY_MIN_DOLLAR_VOLUME = 5_000_000.0
COMPANY_DOLLAR_VOLUME_WINDOW = 20
COMPANY_DOLLAR_VOLUME_MIN_OBS = 10
COMPANY_WEIGHTS = (("x5_sector", 0.35), ("rvol5", 0.25), ("att", 0.20), ("pth", 0.20))
COMPANY_TOP_N = 50
COMPANY_ATTENTION_ONLY = 2.0

# Diagnostics.
BENCHMARK_GAP_REPORT_LIMIT = 20

_MAD_SCALE = 1.4826
_EPS = 1e-12


# ---------------------------------------------------------------------------
# Public helpers (reused by proposals and tests)
# ---------------------------------------------------------------------------


def robust_z(
    series: pd.Series,
    mask: Optional[Iterable[bool]] = None,
    *,
    clip: Optional[float] = Z_CLIP,
) -> pd.Series:
    """Robust z = (v - median) / (1.4826 MAD), clipped to +-``clip``.

    Median and MAD come from the non-NaN entries selected by ``mask`` (default: all),
    so values outside the reference set are scored against it. MAD = 0 falls back to
    the population standard deviation; when both are 0 every value scores 0.
    """
    values = pd.to_numeric(series, errors="coerce").astype(float)
    reference = (
        np.ones(len(values), dtype=bool)
        if mask is None
        else np.asarray(list(mask) if not isinstance(mask, (np.ndarray, pd.Series)) else mask, dtype=bool)
    )
    z = _robust_z_matrix(values.to_numpy()[:, None], reference.reshape(-1, 1), clip=clip)
    return pd.Series(z[:, 0], index=series.index, dtype=float)


def capped_weights(weights: pd.Series, cap: float = MAX_MEMBER_WEIGHT) -> pd.Series:
    """Normalize positive weights to sum 1 with no entry above ``cap`` (see ``_capped_weights``)."""
    raw = pd.to_numeric(weights, errors="coerce").astype(float)
    capped = _capped_weights(raw.to_numpy()[None, :], cap=cap)[0]
    return pd.Series(capped, index=weights.index, dtype=float)


def excess_log_return(close: pd.DataFrame | pd.Series, benchmark: pd.Series, k: int) -> pd.DataFrame | pd.Series:
    """ln(P_d / P_{d-k}) - ln(SPY_d / SPY_{d-k}) over ``k`` rows of the session index."""
    log_close = np.log(close.where(close > 0))
    log_benchmark = np.log(benchmark.reindex(close.index).where(benchmark.reindex(close.index) > 0))
    benchmark_change = log_benchmark - log_benchmark.shift(k)
    change = log_close - log_close.shift(k)
    if isinstance(change, pd.DataFrame):
        return change.sub(benchmark_change, axis=0)
    return change - benchmark_change


def relative_volume_1d(volume: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    """Today's volume over the mean of the prior 20 sessions (non-positive volume is missing)."""
    clean = volume.where(volume > 0)
    base = clean.shift(1).rolling(RVOL1_WINDOW, min_periods=RVOL1_MIN_OBS).mean()
    return clean / base


def relative_volume_5d(volume: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    """rvol5 = ln(mean(volume, last 5) / median(volume, last 60)), both windows ending today."""
    clean = volume.where(volume > 0)
    short = clean.rolling(RVOL5_SHORT_WINDOW, min_periods=RVOL5_SHORT_MIN_OBS).mean()
    long = clean.rolling(RVOL5_LONG_WINDOW, min_periods=RVOL5_LONG_MIN_OBS).median()
    return np.log(short / long)


def near_high_ratio(close: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    """pth = close / rolling max(close, 252 sessions, min 126 observations)."""
    clean = close.where(close > 0)
    return clean / clean.rolling(HIGH_WINDOW, min_periods=HIGH_MIN_OBS).max()


def ignition_flags(close: pd.DataFrame, volume: pd.DataFrame) -> pd.DataFrame:
    """True where the 1-session return is >= +5% and rvol1 >= 2."""
    clean = close.where(close > 0)
    returns = clean / clean.shift(1) - 1.0
    rvol1 = relative_volume_1d(volume.reindex(index=close.index, columns=close.columns))
    return (returns >= IGNITION_RETURN) & (rvol1 >= IGNITION_RVOL)


def attention_star(counts: pd.DataFrame, sessions: Sequence[date]) -> pd.DataFrame:
    """Market-demeaned news-attention z (a*) per session; columns follow ``counts``.

    ``counts`` is UTC days x tickers (complete days zero-filled, missing days NaN rows).
    For session d: c7 = counts over UTC days d-6..d (all seven required), l = ln(1+c7),
    a = (l_d - median l[d-97..d-8]) / max(1.4826 MAD, 0.3) with >= 60 baseline
    observations, a* = clip(a - median over tickers with non-NaN a, +-4).
    """
    session_list = [_as_date(value) for value in sessions]
    raw = _attention_z(counts, session_list)
    values = raw.to_numpy(dtype=float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        center = np.nanmedian(values, axis=1) if values.shape[1] else np.full(len(values), np.nan)
    star = np.clip(values - center[:, None], -ATT_CLIP, ATT_CLIP)
    return pd.DataFrame(star, index=raw.index, columns=raw.columns)


def heat_ewma(values: Sequence[float] | np.ndarray | pd.Series, half_life: float) -> np.ndarray:
    """EWMA with lambda = 0.5^(1/h), seeded by the first non-NaN value; NaN inputs carry."""
    array = np.asarray(values, dtype=float)
    return _ewma_carry(array[None, :], half_life)[0]


def apply_stage_hold(
    candidates: Sequence[str],
    hold: int = STAGE_HOLD,
    *,
    fixed: Optional[Sequence[Optional[str]]] = None,
) -> list[str]:
    """Displayed stage from candidate stages (spec §Scoring "Flags and stages").

    The series starts at the first candidate. A different candidate replaces the
    displayed stage only after it held ``hold`` consecutive sessions, except that
    "emerging" switches immediately. "insufficient" is a data condition rather than a
    view: it shows at once, breaks any run of consecutive candidates, and leaves the
    view underneath intact, so when the theme is rankable again the hold resumes from
    the last displayed stage ("neutral" if the series began unrankable). A one-session
    data gap therefore never fast-tracks a new stage.

    ``fixed`` pins the displayed stage where it is not None (stages already
    published); the sessions after it continue the hold from the pinned values. The
    view then starts from the published record: stages recomputed before the first
    pin were never displayed (and may rest on retroactive membership), so a record
    that begins "insufficient" resumes from "neutral" rather than from them.
    """
    stages: list[str] = []
    view: Optional[str] = None  # the displayed stage of the latest rankable session
    pinned_seen = False
    for index, candidate in enumerate(candidates):
        pinned = fixed[index] if fixed is not None else None
        if pinned is not None:
            if not pinned_seen:
                view, pinned_seen = None, True  # the published record starts here
            stage = pinned
        elif candidate == "insufficient":
            stage = "insufficient"
        elif view is None or candidate == view or candidate == "emerging":
            stage = candidate
        else:
            window = candidates[max(0, index - hold + 1): index + 1]
            held = len(window) == hold and all(value == candidate for value in window)
            stage = candidate if held else view
        stages.append(stage)
        if stage != "insufficient":
            view = stage
        elif view is None:
            view = "neutral"
    return stages


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def score_themes(
    inputs: ScoringInputs,
    *,
    published: Optional[Iterable[Mapping[str, Any]]] = None,
    pin_output_dates: bool = False,
) -> ScoringResult:
    """Score every theme over the input window and emit rows for the trailing sessions.

    Theme rows cover the last ``inputs.output_sessions`` sessions; member and company
    rows cover the as-of session only. All row values are plain Python scalars, lists
    or dicts (floats are finite or None) so they insert without conversion.

    ``published`` holds theme rows already published for earlier sessions (keys
    ``theme_slug``, ``date``, ``stage`` and optionally ``candidate_stage``,
    ``rank_now``, ``streak_top5``). Rows dated before the first output session pin
    those sessions, so ``stage_prev``, ``stage_changed``, the stage hold,
    ``emerging_first_20``, ``rank_change`` and ``streak_top5`` continue the published
    history. Without them every "previous session" value is recomputed from this
    run's window, whose start shifts the EWMA seeds and trailing baselines, so it can
    differ slightly from what was published for that session.

    ``pin_output_dates=True`` lets rows dated inside the output window pin their
    sessions too (never the as-of session): a backfill passes the dates it keeps
    published, so the dates it does publish continue those served rows.
    """
    min_members = int(settings.THEME_MIN_RANKABLE_MEMBERS)
    sessions = _session_list(inputs)
    diagnostics: dict[str, Any] = {"scoring_version": SCORING_VERSION, "min_rankable_members": min_members}
    if not sessions:
        diagnostics["status"] = "no-sessions"
        return ScoringResult(theme_rows=[], member_rows=[], company_rows=[], diagnostics=diagnostics)
    as_of = sessions[-1]
    n_sessions = len(sessions)
    themes = sorted({theme.slug: theme for theme in inputs.themes}.values(), key=lambda theme: theme.slug)
    theme_slugs = {theme.slug for theme in themes}
    spans_by_slug: dict[str, list[MemberSpan]] = defaultdict(list)
    for span in inputs.members:
        if span.theme_slug in theme_slugs:
            spans_by_slug[span.theme_slug].append(span)

    price_columns = [] if inputs.close is None else [str(column) for column in inputs.close.columns]
    member_tickers = {span.ticker for spans in spans_by_slug.values() for span in spans}
    tickers = sorted(set(price_columns) | member_tickers)
    column_of = {ticker: index for index, ticker in enumerate(tickers)}

    with np.errstate(divide="ignore", invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        close = _wide(inputs.close, sessions, tickers)
        close[close <= 0] = np.nan
        volume = _wide(inputs.volume, sessions, tickers)
        volume[volume <= 0] = np.nan
        benchmark = _series(inputs.benchmark, sessions)
        benchmark[benchmark <= 0] = np.nan
        attention = attention_star(inputs.attention_counts, sessions)
        att = attention.reindex(columns=tickers).to_numpy(dtype=float) if len(attention.columns) else np.full(
            (n_sessions, len(tickers)), np.nan
        )
        day_reasons = _attention_day_reasons(inputs.attention_counts, inputs.attention_totals, sessions)
        features = _ticker_features(close, volume, benchmark, att)

        theme_baskets = [
            _theme_basket(theme.slug, spans_by_slug.get(theme.slug, []), sessions, as_of, column_of)
            for theme in themes
        ]
        industry_baskets = _industry_baskets(inputs, column_of, n_sessions)
        baskets = theme_baskets + industry_baskets
        basket_features = [_basket_features(basket, features, benchmark) for basket in baskets]
        n_themes = len(theme_baskets)

        stacked = {
            name: np.vstack([values[name] for values in basket_features]) if baskets else np.empty((0, n_sessions))
            for name in _BASKET_SERIES
        }
        n_priced = stacked["n_priced"]
        rankable = np.zeros((len(baskets), n_sessions), dtype=bool)
        rankable[:n_themes] = n_priced[:n_themes] >= min_members
        comparison = rankable.copy()
        comparison[n_themes:] = n_priced[n_themes:] >= INDUSTRY_MIN_MEMBERS

        z = {
            name: _robust_z_matrix(stacked[name], comparison)
            for name in ("x5", "x21", "x63", "b50", "nh", "d_b50", "rvol5_median", "att", "att_breadth")
        }
        score_price = sum(weight * z[name] for name, weight in W_P)
        score_breadth = sum(weight * z[name] for name, weight in W_B)
        score_volume = z["rvol5_median"]
        score_attention = sum(weight * z[name] for name, weight in W_A)

        day_degraded = np.array([bool(reasons) for reasons in day_reasons], dtype=bool)
        coverage = stacked["att_coverage"]
        # Member-level reasons need priced members: a basket with none has no heat to
        # degrade (e.g. a theme added after the last membership refresh).
        has_priced = n_priced > 0
        low_coverage = has_priced & ~(coverage >= ATT_MIN_MEMBER_COVERAGE)
        unavailable = has_priced & np.isnan(score_attention)
        degraded = day_degraded[None, :] | low_coverage | unavailable
        weights_h = dict(W_H)
        h_full = (
            weights_h["P"] * score_price
            + weights_h["B"] * score_breadth
            + weights_h["V"] * score_volume
            + weights_h["A"] * score_attention
        )
        without_attention = 1.0 - weights_h["A"]
        h_degraded = (
            weights_h["P"] * score_price + weights_h["B"] * score_breadth + weights_h["V"] * score_volume
        ) / without_attention
        h_raw = np.where(degraded, h_degraded, h_full)
        heat = 100.0 / (1.0 + np.exp(-h_raw))
        heat_now = _ewma_carry(heat, HALF_LIFE_NOW)
        heat_trend = _ewma_carry(heat, HALF_LIFE_TREND)
        pct_now = _cross_percentile(heat_now, comparison)
        pct_trend = _cross_percentile(heat_trend, comparison)
        pct_x63 = _cross_percentile(stacked["x63"], comparison)

        slugs = [theme.slug for theme in themes]
        theme_rankable = rankable[:n_themes]
        rank_now = _rank_rows(heat_now[:n_themes], theme_rankable)
        rank_trend = _rank_rows(heat_trend[:n_themes], theme_rankable)

        output_count = min(max(1, int(inputs.output_sessions or 1)), n_sessions)
        output_positions = list(range(n_sessions - output_count, n_sessions))
        pinned = _published_by_position(
            published, sessions, n_sessions - 1 if pin_output_dates else output_positions[0]
        )

        proxy = _proxy_series(inputs, themes, sessions, benchmark)
        session_dates = np.array(sessions, dtype="datetime64[D]")
        theme_series = []
        for index, (theme, basket) in enumerate(zip(themes, theme_baskets)):
            theme_series.append(
                _theme_time_series(
                    index=index,
                    theme=theme,
                    basket=basket,
                    features=features,
                    benchmark=benchmark,
                    session_dates=session_dates,
                    stacked=stacked,
                    rankable=theme_rankable[index],
                    score_price=score_price[index],
                    score_breadth=score_breadth[index],
                    z_att=z["att"][index],
                    h_raw=h_raw[index],
                    heat_now=heat_now[index],
                    heat_trend=heat_trend[index],
                    pct_now=pct_now[index],
                    pct_trend=pct_trend[index],
                    pct_x63=pct_x63[index],
                    rank_now=rank_now[index],
                    proxy=proxy[index],
                    published=pinned.get(theme.slug, {}),
                )
            )

        leaders_by_theme = [
            _leaders(basket, features, output_positions) for basket in theme_baskets
        ]

        theme_rows: list[dict[str, Any]] = []
        for position in output_positions:
            for index, theme in enumerate(themes):
                series = theme_series[index]
                group_reasons = list(day_reasons[position])
                if low_coverage[index, position]:
                    group_reasons.append("member_coverage")
                if unavailable[index, position] and not group_reasons:
                    group_reasons.append("attention_unavailable")
                is_degraded = bool(degraded[index, position])
                theme_rows.append(
                    {
                        "theme_slug": theme.slug,
                        "date": sessions[position],
                        "pit": bool(theme_baskets[index].pit[position]),
                        "rankable": bool(theme_rankable[index, position]),
                        "n_members": int(stacked["n_members"][index, position]),
                        "n_priced": int(stacked["n_priced"][index, position]),
                        "ret_1d": _float(stacked["ret_1d"][index, position]),
                        "basket_index": _float(stacked["basket_index"][index, position]),
                        "x5": _float(stacked["x5"][index, position]),
                        "x21": _float(stacked["x21"][index, position]),
                        "x63": _float(stacked["x63"][index, position]),
                        "x126": _float(stacked["x126"][index, position]),
                        "b50": _float(stacked["b50"][index, position]),
                        "nh": _float(stacked["nh"][index, position]),
                        "d_b50": _float(stacked["d_b50"][index, position]),
                        "ign": int(stacked["ign"][index, position]),
                        "ign5": int(stacked["ign5"][index, position]),
                        "rvol5_median": _float(stacked["rvol5_median"][index, position]),
                        "att": _float(stacked["att"][index, position]),
                        "att_breadth": _float(stacked["att_breadth"][index, position]),
                        "score_price": _float(score_price[index, position]),
                        "score_breadth": _float(score_breadth[index, position]),
                        "score_volume": _float(score_volume[index, position]),
                        "score_attention": None if is_degraded else _float(score_attention[index, position]),
                        "h_raw": _float(h_raw[index, position]),
                        "heat": _float(heat[index, position]),
                        "heat_now": _float(heat_now[index, position]),
                        "heat_trend": _float(heat_trend[index, position]),
                        "pct_now": _float(pct_now[index, position]),
                        "pct_trend": _float(pct_trend[index, position]),
                        "rank_now": _int(rank_now[index, position]),
                        "rank_trend": _int(rank_trend[index, position]),
                        "rank_change": _int(series["rank_change"][position]),
                        "streak_top5": int(series["streak_top5"][position]),
                        "ts_z": _float(series["ts_z"][position]),
                        "cocorr": _float(series["cocorr"][position]),
                        "cocorr_pct": _float(series["cocorr_pct"][position]),
                        "emerge": bool(series["emerge"][position]),
                        "attention_only": bool(series["attention_only"][position]),
                        "attention_degraded": is_degraded,
                        "candidate_stage": series["candidate_stage"][position],
                        "stage": series["stage"][position],
                        "stage_prev": series["stage"][position - 1] if position > 0 else None,
                        "stage_changed": bool(series["stage_changed"][position]),
                        "emerging_first_20": bool(series["emerging_first_20"][position]),
                        "proxy_etf": series["proxy_etf"][position],
                        "proxy_x21": _float(series["proxy_x21"][position]),
                        "leaders": leaders_by_theme[index][position],
                        "flags": {
                            "insufficient_members": not bool(theme_rankable[index, position]),
                            "attention_degraded_reasons": group_reasons if is_degraded else [],
                            "attention_coverage": _float(coverage[index, position]),
                        },
                    }
                )

        member_rows = []
        theme_slugs_by_ticker: dict[str, list[str]] = defaultdict(list)
        for index, (theme, basket) in enumerate(zip(themes, theme_baskets)):
            rows = _member_rows(theme.slug, basket, basket_features[index], spans_by_slug.get(theme.slug, []),
                                features, sessions, as_of)
            member_rows.extend(rows)
            for row in rows:
                theme_slugs_by_ticker[row["ticker"]].append(theme.slug)

        company_rows, eligible_count = _company_rows(inputs, features, column_of, sessions, theme_slugs_by_ticker)

    as_of_position = n_sessions - 1
    active_member_tickers = sorted({row["ticker"] for row in member_rows})
    priced_member_tickers = [
        ticker for ticker in active_member_tickers
        if np.isfinite(close[as_of_position, column_of[ticker]])
    ]
    diagnostics.update(
        {
            "status": "scored",
            "as_of": as_of.isoformat(),
            "requested_as_of": _as_date(inputs.as_of).isoformat(),
            "sessions": n_sessions,
            "output_sessions": output_count,
            "output_start": sessions[output_positions[0]].isoformat(),
            "tickers": len(tickers),
            "tickers_priced_as_of": int(np.isfinite(close[as_of_position]).sum()),
            "benchmark_priced_as_of": bool(np.isfinite(benchmark[as_of_position])),
            **_benchmark_gaps(benchmark, sessions, output_positions),
            "published_sessions_pinned": len({position for rows in pinned.values() for position in rows}),
            "themes": n_themes,
            "rankable_themes": int(theme_rankable[:, as_of_position].sum()) if n_themes else 0,
            "comparison_industries": int(comparison[n_themes:, as_of_position].sum()) if industry_baskets else 0,
            "active_member_tickers": len(active_member_tickers),
            "priced_member_tickers": len(priced_member_tickers),
            "member_price_coverage": (
                len(priced_member_tickers) / len(active_member_tickers) if active_member_tickers else None
            ),
            "attention_day_reasons": list(day_reasons[as_of_position]),
            # Feed-level problem at as_of (a re-run after news_attention publishes can fix it).
            "attention_day_degraded": bool(day_degraded[as_of_position]),
            # Any rankable theme scored without attention at as_of. Unrankable themes (e.g.
            # no members, hence no attention coverage) must not flag the whole run.
            "attention_degraded": bool(day_degraded[as_of_position])
            or bool((degraded[:n_themes, as_of_position] & theme_rankable[:, as_of_position]).any()),
            "attention_degraded_themes": int(
                (degraded[:n_themes, as_of_position] & theme_rankable[:, as_of_position]).sum()
            ),
            "attention_degraded_sessions": int(day_degraded[output_positions].sum()),
            "companies_eligible": eligible_count,
            "companies_emitted": len(company_rows),
        }
    )
    return ScoringResult(
        theme_rows=theme_rows,
        member_rows=member_rows,
        company_rows=company_rows,
        diagnostics=diagnostics,
    )


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


def _session_list(inputs: ScoringInputs) -> list[date]:
    raw = inputs.sessions if inputs.sessions else ([] if inputs.close is None else list(inputs.close.index))
    as_of = _as_date(inputs.as_of)
    return sorted({_as_date(value) for value in raw if _as_date(value) <= as_of})


def _date_indexed(frame: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    out = frame.copy(deep=False)
    out.index = pd.Index([_as_date(value) for value in out.index], dtype=object)
    out = out[~out.index.duplicated(keep="last")]
    if isinstance(out, pd.DataFrame):
        out.columns = [str(column) for column in out.columns]
        out = out.loc[:, ~out.columns.duplicated(keep="last")]
    return out


def _wide(frame: Optional[pd.DataFrame], sessions: list[date], tickers: list[str]) -> np.ndarray:
    if frame is None or frame.shape[0] == 0 or frame.shape[1] == 0:
        return np.full((len(sessions), len(tickers)), np.nan)
    aligned = _date_indexed(frame).reindex(index=sessions, columns=tickers)
    try:
        values = aligned.to_numpy(dtype=float)
    except (TypeError, ValueError):
        values = aligned.apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    values = values.copy()
    values[~np.isfinite(values)] = np.nan
    return values


def _series(series: Optional[pd.Series], sessions: list[date]) -> np.ndarray:
    if series is None or len(series) == 0:
        return np.full(len(sessions), np.nan)
    values = pd.to_numeric(_date_indexed(series).reindex(sessions), errors="coerce").to_numpy(dtype=float)
    values = values.copy()
    values[~np.isfinite(values)] = np.nan
    return values


def _shift(values: np.ndarray, periods: int) -> np.ndarray:
    out = np.full(values.shape, np.nan)
    if periods == 0:
        return values.astype(float, copy=True)
    if 0 < periods < len(values):
        out[periods:] = values[:-periods]
    return out


def _published_by_position(
    rows: Optional[Iterable[Mapping[str, Any]]],
    sessions: list[date],
    first_output: int,
) -> dict[str, dict[int, dict[str, Any]]]:
    """Published theme rows by slug and window position, for sessions before ``first_output``.

    By default ``first_output`` is the first output session: output sessions are being
    (re)scored, so their published rows pin nothing. ``score_themes(pin_output_dates=True)``
    passes the as-of position instead, so only the as-of session stays unpinned.
    ``rank_now`` / ``streak_top5`` are kept only when the row carries the key, so a
    caller that passes stages alone does not blank the recomputed ranks.
    """
    if rows is None:
        return {}
    position_of = {session: position for position, session in enumerate(sessions[:first_output])}
    out: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        try:
            slug = str(row["theme_slug"])
            position = position_of.get(_as_date(row["date"]))
        except (KeyError, TypeError, ValueError):
            continue
        stage = row.get("stage")
        if position is None or stage not in STAGES:
            continue
        candidate = row.get("candidate_stage")
        pinned: dict[str, Any] = {"stage": stage, "candidate_stage": candidate if candidate in STAGES else None}
        for key in ("rank_now", "streak_top5"):
            if key in row:
                pinned[key] = _int(row[key])
        out[slug][position] = pinned
    return dict(out)


# The excess-return lags behind the price score P (x5, x21, x63).
_PRICE_LAGS = tuple(sorted(int(name[1:]) for name, _ in W_P))


def _benchmark_gaps(benchmark: np.ndarray, sessions: list[date], output_positions: list[int]) -> dict[str, Any]:
    """Where SPY lacks a close and which output sessions lose P (hence heat) because of it.

    x_k needs SPY at d and d-k, so one missing SPY close leaves every basket's P
    undefined at d, d+5, d+21 and d+63. The values stay NaN (no filling); these keys
    let the pipeline name the cause.
    """
    missing = ~np.isfinite(benchmark)
    as_of_position = len(sessions) - 1

    def lacks(position: int, lag: int) -> bool:
        return position - lag >= 0 and bool(missing[position - lag])

    gaps = np.flatnonzero(missing)
    blocked = [
        sessions[position].isoformat()
        for position in output_positions
        if any(lacks(position, lag) for lag in (0, *_PRICE_LAGS))
    ]
    return {
        "benchmark_missing_sessions": int(gaps.size),
        "benchmark_missing_dates": [sessions[position].isoformat() for position in gaps[-BENCHMARK_GAP_REPORT_LIMIT:]],
        "benchmark_missing_lags": [lag for lag in _PRICE_LAGS if lacks(as_of_position, lag)],
        "benchmark_blocked_dates": blocked[-BENCHMARK_GAP_REPORT_LIMIT:],
    }


# ---------------------------------------------------------------------------
# Per-ticker features
# ---------------------------------------------------------------------------


def _rolling_mean_observations(values: np.ndarray, window: int) -> np.ndarray:
    """Per column, the mean of the last ``window`` observations (NaN rows skipped).

    This is how ``market_breadth.build_price_feature_frame`` rolls its MAs (over the
    rows a ticker has), so one missing bar costs that session only instead of
    ``window`` sessions. The columns are laid end to end and rolled in one pass;
    windows that would reach into the previous column are discarded. NaN where the
    row has no value or fewer than ``window`` observations precede it.
    """
    present = ~np.isnan(values).T  # columns x rows
    flat = values.T[present]  # observations column by column, in session order
    means = pd.Series(flat, dtype=float).rolling(window, min_periods=window).mean().to_numpy()
    observation = (np.cumsum(present, axis=1) - 1)[present]  # observation number within its column
    means[observation < window - 1] = np.nan
    out = np.full(present.shape, np.nan)
    out[present] = means
    return out.T


@dataclass
class _TickerFeatures:
    close: np.ndarray
    volume: np.ndarray
    ret: np.ndarray  # simple 1-session return, NaN unless both closes exist
    ret_clipped: np.ndarray
    above_ma50: np.ndarray  # 1.0 / 0.0 / NaN
    near_high: np.ndarray  # 1.0 / 0.0 / NaN
    pth: np.ndarray
    rvol5: np.ndarray
    ignited: np.ndarray  # bool
    ignited_recent: np.ndarray  # bool, ignited in any of the last 5 sessions
    att: np.ndarray  # a*
    x5: np.ndarray
    x21: np.ndarray


def _ticker_features(close: np.ndarray, volume: np.ndarray, benchmark: np.ndarray, att: np.ndarray) -> _TickerFeatures:
    close_frame = pd.DataFrame(close)
    volume_frame = pd.DataFrame(volume)
    ret = close / _shift(close, 1) - 1.0
    ma50 = _rolling_mean_observations(close, MA_WINDOW)  # "MA50 needs 50 obs"
    above_ma50 = np.where(np.isnan(close) | np.isnan(ma50), np.nan, (close > ma50).astype(float))
    high = close_frame.rolling(HIGH_WINDOW, min_periods=HIGH_MIN_OBS).max().to_numpy()
    pth = close / high
    near_high = np.where(np.isnan(pth), np.nan, (close >= NEAR_HIGH * high).astype(float))
    base = volume_frame.shift(1).rolling(RVOL1_WINDOW, min_periods=RVOL1_MIN_OBS).mean().to_numpy()
    rvol1 = volume / base
    short = volume_frame.rolling(RVOL5_SHORT_WINDOW, min_periods=RVOL5_SHORT_MIN_OBS).mean().to_numpy()
    long = volume_frame.rolling(RVOL5_LONG_WINDOW, min_periods=RVOL5_LONG_MIN_OBS).median().to_numpy()
    rvol5 = np.log(short / long)
    rvol5[~np.isfinite(rvol5)] = np.nan
    ignited = (ret >= IGNITION_RETURN) & (rvol1 >= IGNITION_RVOL)
    ignited_recent = (
        pd.DataFrame(ignited.astype(float))
        .rolling(IGNITION_RECENT_SESSIONS, min_periods=1)
        .max()
        .to_numpy()
        > 0
    )
    log_close = np.log(close)
    log_benchmark = np.log(benchmark)
    excess = {}
    for k in (5, 21):
        excess[k] = (log_close - _shift(log_close, k)) - (log_benchmark - _shift(log_benchmark, k))[:, None]
    return _TickerFeatures(
        close=close,
        volume=volume,
        ret=ret,
        ret_clipped=np.clip(ret, -RETURN_CLIP, RETURN_CLIP),
        above_ma50=above_ma50,
        near_high=near_high,
        pth=pth,
        rvol5=rvol5,
        ignited=ignited,
        ignited_recent=ignited_recent,
        att=att,
        x5=excess[5],
        x21=excess[21],
    )


# ---------------------------------------------------------------------------
# Attention
# ---------------------------------------------------------------------------


def _day_grid(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _attention_z(counts: Optional[pd.DataFrame], sessions: list[date]) -> pd.DataFrame:
    """Per-ticker attention z (before cross-sectional demeaning), indexed by session."""
    columns = [] if counts is None else [str(column) for column in counts.columns]
    if not sessions or counts is None or counts.shape[0] == 0 or counts.shape[1] == 0:
        return pd.DataFrame(np.full((len(sessions), len(columns)), np.nan), index=sessions, columns=columns)
    frame = _date_indexed(counts)
    columns = list(frame.columns)
    lead = ATT_BASELINE_FIRST_LAG + ATT_WINDOW_DAYS - 1
    start = sessions[0] - timedelta(days=lead)
    grid = frame.reindex(_day_grid(start, sessions[-1]))
    try:
        daily = grid.to_numpy(dtype=float)
    except (TypeError, ValueError):
        daily = grid.apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    daily = np.where(np.isfinite(daily) & (daily >= 0), daily, np.nan)
    c7 = pd.DataFrame(daily).rolling(ATT_WINDOW_DAYS, min_periods=ATT_WINDOW_DAYS).sum().to_numpy()
    level = np.log1p(c7)
    baseline_days = ATT_BASELINE_FIRST_LAG - ATT_BASELINE_LAST_LAG + 1
    windows = sliding_window_view(level, baseline_days, axis=0)  # (days - 89, tickers, 90)
    out = np.full((len(sessions), len(columns)), np.nan)
    for row, session in enumerate(sessions):
        position = (session - start).days
        current = level[position]
        if np.isnan(current).all():
            continue
        median, mad = _window_median_mad(windows[position - ATT_BASELINE_FIRST_LAG], ATT_BASELINE_MIN_OBS)
        scale = np.maximum(_MAD_SCALE * mad, ATT_MAD_FLOOR)
        out[row] = (current - median) / scale
    return pd.DataFrame(out, index=sessions, columns=columns)


def _window_median_mad(window: np.ndarray, min_obs: int) -> tuple[np.ndarray, np.ndarray]:
    """Row-wise median and MAD of a (tickers, days) window ignoring NaN (np.partition puts NaN last)."""
    counts = (~np.isnan(window)).sum(axis=1)
    median = np.full(window.shape[0], np.nan)
    mad = np.full(window.shape[0], np.nan)
    for count in np.unique(counts):
        if count < min_obs:
            continue
        rows = counts == count
        subset = window[rows]
        lower, upper = (int(count) - 1) // 2, int(count) // 2
        ordered = np.partition(subset, (lower, upper), axis=1)
        center = 0.5 * (ordered[:, lower] + ordered[:, upper])
        deviations = np.partition(np.abs(subset - center[:, None]), (lower, upper), axis=1)
        median[rows] = center
        mad[rows] = 0.5 * (deviations[:, lower] + deviations[:, upper])
    return median, mad


def _attention_day_reasons(
    counts: Optional[pd.DataFrame],
    totals: Optional[pd.Series],
    sessions: list[date],
) -> list[list[str]]:
    """Session-level attention degradation reasons (empty list = healthy)."""
    start = sessions[0] - timedelta(days=ATT_TOTAL_BASELINE_DAYS + 2 * ATT_WINDOW_DAYS)
    days = _day_grid(start, sessions[-1])
    known: list[np.ndarray] = []
    if counts is not None and counts.shape[1] > 0:
        frame = _date_indexed(counts) if counts.shape[0] else counts
        known.append(frame.reindex(days).notna().any(axis=1).to_numpy())
    total_values = _series(totals, days) if totals is not None and len(totals) else None
    if total_values is not None:
        known.append(~np.isnan(total_values))
    complete = np.logical_and.reduce(known) if known else np.zeros(len(days), dtype=bool)
    out_of_band = np.zeros(len(days), dtype=bool)
    if total_values is not None:
        weekly = pd.Series(total_values).rolling(ATT_WINDOW_DAYS, min_periods=ATT_WINDOW_DAYS).sum()
        baseline = weekly.shift(1).rolling(ATT_TOTAL_BASELINE_DAYS, min_periods=ATT_TOTAL_BASELINE_MIN_OBS).median()
        weekly_values, baseline_values = weekly.to_numpy(), baseline.to_numpy()
        checkable = np.isfinite(weekly_values) & np.isfinite(baseline_values) & (baseline_values > 0)
        low, high = ATT_TOTAL_BAND
        with np.errstate(invalid="ignore"):
            out_of_band = checkable & (
                (weekly_values < low * baseline_values) | (weekly_values > high * baseline_values)
            )
    reasons: list[list[str]] = []
    for session in sessions:
        position = (session - start).days
        session_reasons = []
        if not complete[position - ATT_WINDOW_DAYS + 1: position + 1].all():
            session_reasons.append("incomplete_news_days")
        if out_of_band[position]:
            session_reasons.append("news_volume_band")
        reasons.append(session_reasons)
    return reasons


# ---------------------------------------------------------------------------
# Baskets
# ---------------------------------------------------------------------------


@dataclass
class _Basket:
    key: str
    tickers: list[str]
    cols: np.ndarray  # indices into the ticker axis
    active: np.ndarray  # sessions x members, membership after the PIT fallback
    raw_weight: np.ndarray  # sessions x members, tier weight where active
    pit: np.ndarray  # sessions, False where today's membership stood in


def _theme_basket(
    slug: str,
    spans: list[MemberSpan],
    sessions: list[date],
    as_of: date,
    column_of: dict[str, int],
) -> _Basket:
    tickers = sorted({span.ticker for span in spans})
    member_index = {ticker: index for index, ticker in enumerate(tickers)}
    session_dates = np.array(sessions, dtype="datetime64[D]")
    active = np.zeros((len(sessions), len(tickers)), dtype=bool)
    raw_weight = np.zeros((len(sessions), len(tickers)))
    current_active = np.zeros(len(tickers), dtype=bool)
    current_weight = np.zeros(len(tickers))
    for span in spans:
        column = member_index[span.ticker]
        weight = float(span.weight) if span.weight is not None and math.isfinite(float(span.weight)) else 0.0
        weight = max(weight, 0.0)
        on = session_dates >= np.datetime64(span.valid_from, "D")
        if span.valid_to is not None:
            on &= session_dates < np.datetime64(span.valid_to, "D")
        active[on, column] = True
        raw_weight[on, column] = np.maximum(raw_weight[on, column], weight)
        if span.active_on(as_of):
            current_active[column] = True
            current_weight[column] = max(current_weight[column], weight)
    if spans:
        first_from = np.datetime64(min(span.valid_from for span in spans), "D")
        pit = session_dates >= first_from
    else:
        pit = np.zeros(len(sessions), dtype=bool)
    # Dates before the theme's first interval borrow today's members (pit=False).
    active[~pit] = current_active
    raw_weight[~pit] = current_weight
    return _Basket(
        key=slug,
        tickers=tickers,
        cols=np.array([column_of[ticker] for ticker in tickers], dtype=int),
        active=active,
        raw_weight=raw_weight,
        pit=pit,
    )


def _industry_baskets(inputs: ScoringInputs, column_of: dict[str, int], n_sessions: int) -> list[_Basket]:
    universe = {str(ticker) for ticker in inputs.universe}
    members: dict[str, list[str]] = defaultdict(list)
    for ticker, industry in (inputs.industries or {}).items():
        ticker = str(ticker)
        if not industry or ticker not in column_of or (universe and ticker not in universe):
            continue
        members[str(industry)].append(ticker)
    baskets = []
    for industry in sorted(members):
        tickers = sorted(members[industry])
        if len(tickers) < INDUSTRY_MIN_MEMBERS:
            continue
        baskets.append(
            _Basket(
                key=f"industry:{industry}",
                tickers=tickers,
                cols=np.array([column_of[ticker] for ticker in tickers], dtype=int),
                active=np.ones((n_sessions, len(tickers)), dtype=bool),
                raw_weight=np.ones((n_sessions, len(tickers))),
                pit=np.ones(n_sessions, dtype=bool),
            )
        )
    return baskets


_BASKET_SERIES = (
    "n_members", "n_priced", "ret_1d", "basket_index", "x5", "x21", "x63", "x126", "b50", "nh", "d_b50",
    "ign", "ign5", "rvol5_median", "att", "att_breadth", "att_coverage",
)


def _capped_weights(raw: np.ndarray, cap: float = MAX_MEMBER_WEIGHT, passes: int = WEIGHT_CAP_PASSES) -> np.ndarray:
    """Row-normalized weights with no entry above ``cap`` (water-filling, <= ``passes`` rounds).

    Each pass pins the weights above the cap at the cap and rescales the rest to fill
    the remaining mass. Rows with fewer than 1/cap members cannot satisfy the cap and
    fall back to equal weights, the flattest distribution available.
    """
    raw = np.where(np.isfinite(raw) & (raw > 0), raw, 0.0)
    member = raw > 0
    count = member.sum(axis=1)
    total = raw.sum(axis=1, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        weights = np.where(total > 0, raw / total, 0.0)
        pinned = np.zeros_like(member)
        for _ in range(passes):
            over = (weights > cap + _EPS) & ~pinned
            if not over.any():
                break
            pinned |= over
            free = member & ~pinned
            free_mass = 1.0 - cap * pinned.sum(axis=1)
            free_total = np.where(free, weights, 0.0).sum(axis=1)
            scale = np.where(free_total > 0, free_mass / free_total, 0.0)
            weights = np.where(pinned, cap, weights * scale[:, None])
            weights = np.where(member, weights, 0.0)
        infeasible = (count > 0) & (count * cap < 1.0 - _EPS)
        if infeasible.any():
            weights[infeasible] = np.where(member[infeasible], 1.0, 0.0) / count[infeasible][:, None]
        sums = weights.sum(axis=1, keepdims=True)
        weights = np.where(sums > 0, weights / sums, 0.0)
    return weights


def _weighted_mean(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Row-wise Σw·v/Σw over entries with a value and positive weight (NaN when none)."""
    valid = ~np.isnan(values) & (weights > 0)
    w = np.where(valid, weights, 0.0)
    total = w.sum(axis=1)
    numerator = (w * np.where(valid, values, 0.0)).sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(total > 0, numerator / total, np.nan)


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Row-wise weighted median; an exact half split averages the two middle values."""
    if values.shape[1] == 0:
        return np.full(values.shape[0], np.nan)
    valid = ~np.isnan(values) & (weights > 0)
    keyed = np.where(valid, values, np.inf)
    order = np.argsort(keyed, axis=1, kind="stable")
    ordered = np.take_along_axis(keyed, order, axis=1)
    ordered_weights = np.take_along_axis(np.where(valid, weights, 0.0), order, axis=1)
    total = ordered_weights.sum(axis=1)
    cumulative = np.cumsum(ordered_weights, axis=1)
    half = 0.5 * total
    tolerance = 1e-9 * np.maximum(total, _EPS)
    first = np.argmax(cumulative >= (half - tolerance)[:, None], axis=1)
    rows = np.arange(values.shape[0])
    lower = ordered[rows, first]
    following = np.minimum(first + 1, values.shape[1] - 1)
    upper = ordered[rows, following]
    exact = (np.abs(cumulative[rows, first] - half) <= tolerance) & np.isfinite(upper)
    median = np.where(exact, 0.5 * (lower + upper), lower)
    return np.where(total > 0, median, np.nan)


def _basket_features(basket: _Basket, features: _TickerFeatures, benchmark: np.ndarray) -> dict[str, np.ndarray]:
    n_sessions = basket.active.shape[0]
    cols = basket.cols
    ret = features.ret[:, cols]
    priced = basket.active & ~np.isnan(ret) & (basket.raw_weight > 0)
    weights = _capped_weights(np.where(priced, basket.raw_weight, 0.0))
    n_members = basket.active.sum(axis=1)
    n_priced = priced.sum(axis=1)
    ret_1d = _weighted_mean(features.ret_clipped[:, cols], weights)
    level = np.cumprod(1.0 + np.where(np.isnan(ret_1d), 0.0, ret_1d))
    has_index = n_priced > 0
    if n_sessions:
        has_index[0] = n_members[0] > 0  # the index starts at 1.0 on the first window session
    basket_index = np.where(has_index, level, np.nan)
    log_index = np.log(basket_index)
    log_benchmark = np.log(benchmark)
    out: dict[str, np.ndarray] = {
        "n_members": n_members,
        "n_priced": n_priced,
        "ret_1d": ret_1d,
        "basket_index": basket_index,
        "weights": weights,
    }
    for k in EXCESS_WINDOWS:
        out[f"x{k}"] = (log_index - _shift(log_index, k)) - (log_benchmark - _shift(log_benchmark, k))
    out["b50"] = _weighted_mean(features.above_ma50[:, cols], weights)
    out["nh"] = _weighted_mean(features.near_high[:, cols], weights)
    out["d_b50"] = out["b50"] - _shift(out["b50"], D_B50_LAG)
    out["ign"] = (features.ignited[:, cols] & basket.active).sum(axis=1)
    out["ign5"] = (features.ignited_recent[:, cols] & basket.active).sum(axis=1)
    out["rvol5_median"] = _weighted_median(features.rvol5[:, cols], weights)
    att = features.att[:, cols]
    out["att"] = _weighted_mean(att, weights)
    out["att_breadth"] = _weighted_mean(np.where(np.isnan(att), np.nan, (att > ATT_BREADTH_THRESHOLD).astype(float)), weights)
    covered = (~np.isnan(att) & priced).sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        out["att_coverage"] = np.where(n_priced > 0, covered / np.maximum(n_priced, 1), np.nan)
    return out


# ---------------------------------------------------------------------------
# Cross-section and time-series statistics
# ---------------------------------------------------------------------------


def _robust_z_matrix(values: np.ndarray, reference: np.ndarray, clip: Optional[float] = Z_CLIP) -> np.ndarray:
    """Column-wise robust z of ``values`` (items x dates) against the ``reference`` items."""
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return values.copy()
    finite = np.isfinite(values)
    ref = np.where(reference & finite, values, np.nan)
    with warnings.catch_warnings(), np.errstate(divide="ignore", invalid="ignore"):
        warnings.simplefilter("ignore", RuntimeWarning)
        median = np.nanmedian(ref, axis=0)
        mad = np.nanmedian(np.abs(ref - median), axis=0)
        std = np.nanstd(ref, axis=0)
        scale = _MAD_SCALE * mad
        scale = np.where(scale > _EPS, scale, std)
        z = (values - median) / scale
    z = np.where(scale > _EPS, z, 0.0)
    z = np.where(finite & np.isfinite(median), z, np.nan)
    if clip is not None:
        z = np.clip(z, -clip, clip)
    return z


def _ewma_carry(values: np.ndarray, half_life: float) -> np.ndarray:
    decay = 0.5 ** (1.0 / half_life)
    out = np.full(values.shape, np.nan)
    state = np.full(values.shape[0], np.nan)
    for position in range(values.shape[1]):
        current = values[:, position]
        has_value = ~np.isnan(current)
        seeded = ~np.isnan(state)
        state = np.where(
            has_value & seeded,
            decay * state + (1.0 - decay) * current,
            np.where(has_value, current, state),
        )
        out[:, position] = state
    return out


def _cross_percentile(values: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Share of reference values <= v per date (0..1, the highest reference value = 1)."""
    out = np.full(values.shape, np.nan)
    for position in range(values.shape[1]):
        column = values[:, position]
        finite = np.isfinite(column)
        ref = np.sort(column[reference[:, position] & finite])
        if ref.size:
            out[finite, position] = np.searchsorted(ref, column[finite], side="right") / ref.size
    return out


def _rank_rows(values: np.ndarray, eligible: np.ndarray) -> np.ndarray:
    """Per-date dense ranks (1 = highest) among eligible rows; ties keep row (slug) order."""
    ranks = np.full(values.shape, np.nan)
    rows = np.arange(values.shape[0])
    for position in range(values.shape[1]):
        column = values[:, position]
        candidates = rows[eligible[:, position] & np.isfinite(column)]
        if candidates.size:
            order = candidates[np.lexsort((candidates, -column[candidates]))]
            ranks[order, position] = np.arange(1, order.size + 1)
    return ranks


def _prior_windows(series: np.ndarray, window: int) -> np.ndarray:
    """Row t holds series[t-window .. t-1] (NaN-padded)."""
    padded = np.concatenate([np.full(window, np.nan), series])
    return sliding_window_view(padded, window)[: len(series)]


def _trailing_robust_z(series: np.ndarray, window: int = TS_Z_WINDOW, min_obs: int = TS_Z_MIN_OBS) -> np.ndarray:
    prior = _prior_windows(series, window)
    count = (~np.isnan(prior)).sum(axis=1)
    with warnings.catch_warnings(), np.errstate(divide="ignore", invalid="ignore"):
        warnings.simplefilter("ignore", RuntimeWarning)
        median = np.nanmedian(prior, axis=1)
        mad = np.nanmedian(np.abs(prior - median[:, None]), axis=1)
        std = np.nanstd(prior, axis=1)
        scale = _MAD_SCALE * mad
        scale = np.where(scale > _EPS, scale, std)
        z = np.where(scale > _EPS, (series - median) / scale, 0.0)
    return np.where((count >= min_obs) & ~np.isnan(series), z, np.nan)


def _trailing_percentile(series: np.ndarray, window: int = COCORR_PCT_WINDOW, min_obs: int = COCORR_PCT_MIN_OBS) -> np.ndarray:
    prior = _prior_windows(series, window)
    count = (~np.isnan(prior)).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        at_or_below = (prior <= series[:, None]).sum(axis=1)
        pct = at_or_below / count
    return np.where((count >= min_obs) & ~np.isnan(series), pct, np.nan)


def _rolling_sum(values: np.ndarray, window: int) -> np.ndarray:
    cumulative = np.cumsum(values, axis=0)
    out = cumulative.copy()
    out[window:] = cumulative[window:] - cumulative[:-window]
    return out


def _residual_returns(returns: np.ndarray, market: np.ndarray) -> np.ndarray:
    """r - beta·r_SPY with beta from the trailing 120 sessions (>= 60 paired observations)."""
    valid = ~np.isnan(returns) & ~np.isnan(market)[:, None]
    x = np.where(valid, market[:, None], 0.0)
    y = np.where(valid, returns, 0.0)
    n = _rolling_sum(valid.astype(float), BETA_WINDOW)
    sx, sy = _rolling_sum(x, BETA_WINDOW), _rolling_sum(y, BETA_WINDOW)
    sxx, sxy = _rolling_sum(x * x, BETA_WINDOW), _rolling_sum(x * y, BETA_WINDOW)
    with np.errstate(divide="ignore", invalid="ignore"):
        covariance = sxy - sx * sy / n
        variance = sxx - sx * sx / n
        beta = np.where((n >= BETA_MIN_OBS) & (variance > _EPS), covariance / variance, np.nan)
    return returns - beta * market[:, None]


def _cocorr(residuals: np.ndarray, active: np.ndarray) -> np.ndarray:
    """Mean pairwise correlation of active members' residuals over the trailing 60 sessions.

    Members need all 60 residuals. With unit-norm demeaned columns z_i, the sum of the
    correlation matrix is ||Σ z_i||², so the off-diagonal mean is (||Σ z_i||² - m)/(m(m-1)).
    """
    n_sessions = residuals.shape[0]
    out = np.full(n_sessions, np.nan)
    if n_sessions < COCORR_WINDOW or residuals.shape[1] < COCORR_MIN_MEMBERS:
        return out
    windows = sliding_window_view(residuals, COCORR_WINDOW, axis=0)  # (dates, members, 60)
    complete = ~np.isnan(windows).any(axis=2)
    centered = windows - np.where(complete, windows.mean(axis=2), 0.0)[..., None]
    norm = np.sqrt(np.where(complete[..., None], centered * centered, 0.0).sum(axis=2))
    included = complete & (norm > _EPS) & active[COCORR_WINDOW - 1:]
    with np.errstate(divide="ignore", invalid="ignore"):
        unit = np.where(included[..., None], centered / norm[..., None], 0.0)
    total = unit.sum(axis=1)
    m = included.sum(axis=1).astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        mean_corr = ((total * total).sum(axis=1) - m) / (m * (m - 1.0))
    out[COCORR_WINDOW - 1:] = np.where(m >= COCORR_MIN_MEMBERS, mean_corr, np.nan)
    return out


# ---------------------------------------------------------------------------
# Theme series, stages and leaders
# ---------------------------------------------------------------------------


def _proxy_series(
    inputs: ScoringInputs,
    themes: list[ThemeMeta],
    sessions: list[date],
    benchmark: np.ndarray,
) -> list[dict[str, list]]:
    """Per theme and session: the first proxy ETF with a computable x21, else the first priced one."""
    etfs = sorted({str(etf) for theme in themes for etf in theme.proxy_etfs})
    proxy_columns = set() if inputs.proxy_close is None else {str(column) for column in inputs.proxy_close.columns}
    from_proxy = _wide(inputs.proxy_close, sessions, etfs)
    from_close = _wide(inputs.close, sessions, etfs)
    prices = np.where(np.array([etf in proxy_columns for etf in etfs])[None, :], from_proxy, from_close)
    prices[prices <= 0] = np.nan
    log_prices = np.log(prices)
    log_benchmark = np.log(benchmark)
    x21 = (log_prices - _shift(log_prices, 21)) - (log_benchmark - _shift(log_benchmark, 21))[:, None]
    column_of = {etf: index for index, etf in enumerate(etfs)}
    out = []
    for theme in themes:
        names: list[Optional[str]] = [None] * len(sessions)
        values: list[float] = [math.nan] * len(sessions)
        ordered = [str(etf) for etf in theme.proxy_etfs]
        for position in range(len(sessions)):
            fallback = None
            for etf in ordered:
                column = column_of[etf]
                if np.isfinite(x21[position, column]):
                    names[position], values[position] = etf, float(x21[position, column])
                    break
                if fallback is None and np.isfinite(prices[position, column]):
                    fallback = etf
            else:
                names[position] = fallback
        out.append({"proxy_etf": names, "proxy_x21": values})
    return out


def _candidate_stages(
    rankable: np.ndarray,
    crowded: np.ndarray,
    leading: np.ndarray,
    emerging: np.ndarray,
    accelerating: np.ndarray,
    fading: np.ndarray,
) -> list[str]:
    """Priority: crowded > leading > emerging > accelerating > fading > neutral."""
    stages = []
    for position in range(len(rankable)):
        if not rankable[position]:
            stages.append("insufficient")
        elif crowded[position]:
            stages.append("crowded")
        elif leading[position]:
            stages.append("leading")
        elif emerging[position]:
            stages.append("emerging")
        elif accelerating[position]:
            stages.append("accelerating")
        elif fading[position]:
            stages.append("fading")
        else:
            stages.append("neutral")
    return stages


def _trailing_any(flags: np.ndarray, window: int) -> np.ndarray:
    return pd.Series(flags.astype(float)).rolling(window, min_periods=1).max().to_numpy() > 0


def _trailing_count(flags: np.ndarray, window: int) -> np.ndarray:
    return pd.Series(flags.astype(float)).rolling(window, min_periods=1).sum().to_numpy()


def _stage_conditions(
    *,
    rankable: np.ndarray,
    ts_z: np.ndarray,
    pct_now: np.ndarray,
    pct_trend: np.ndarray,
    pct_x63: np.ndarray,
    x21: np.ndarray,
    b50: np.ndarray,
    d_b50: np.ndarray,
    ign5: np.ndarray,
    heat_now: np.ndarray,
    heat_trend: np.ndarray,
    score_price: np.ndarray,
    score_breadth: np.ndarray,
    z_att: np.ndarray,
    cocorr_pct: np.ndarray,
    proxy_age_days: Optional[np.ndarray] = None,
) -> dict[str, Any]:
    """One theme's flags and candidate stages from its per-session series (spec §4).

    Every comparison involving NaN is False. ``pct_x63`` >= 0.75 is "x63 in the top
    quartile", the same percentile convention as the leading rule's pct_trend >= 0.75.
    ``proxy_age_days`` is d minus the proxy ETF's inception date (None = unknown).
    """
    with np.errstate(invalid="ignore"):
        emerge = (
            (ts_z >= EMERGE_TS_Z)
            & (_shift(pct_now, EMERGE_PCT_LAG) < EMERGE_PCT_MAX)
            & (ign5 >= EMERGE_MIN_IGN5)
            & (d_b50 > EMERGE_MIN_D_B50)
            & (x21 > 0)
        )
        attention_only = (z_att >= ATTENTION_ONLY_Z) & (score_price < 0)
        leading = (_trailing_count(pct_trend >= LEADING_PCT, LEADING_WINDOW) >= LEADING_MIN_SESSIONS) & (
            b50 >= LEADING_MIN_B50
        )
        accelerating = (heat_now > heat_trend + STAGE_GAP) & (score_price > ACCEL_MIN_P) & (score_breadth > 0)
        recent_peak = pd.Series(heat_now).rolling(FADING_WINDOW, min_periods=1).max().to_numpy()
        fading = (heat_now < heat_trend - STAGE_GAP) & (heat_now <= recent_peak - FADING_DROP) & (d_b50 < 0)
        emerging = _trailing_any(emerge, EMERGE_LOOKBACK) & (pct_trend < EMERGING_MAX_PCT_TREND)
        crowding = (cocorr_pct >= CROWDED_COCORR_PCT) & (pct_x63 >= CROWDED_X63_PCT)
    if proxy_age_days is not None:
        crowding |= (proxy_age_days >= 0) & (proxy_age_days <= PROXY_INCEPTION_DAYS)
    crowded = (leading | accelerating) & crowding
    return {
        "emerge": emerge,
        "attention_only": attention_only,
        "crowded": crowded,
        "leading": leading,
        "emerging": emerging,
        "accelerating": accelerating,
        "fading": fading,
        "candidates": _candidate_stages(rankable, crowded, leading, emerging, accelerating, fading),
    }


def _stage_transitions(stages: Sequence[str], candidates: Sequence[str]) -> tuple[np.ndarray, np.ndarray]:
    """stage_changed and emerging_first_20 for a displayed-stage series.

    emerging_first_20 also needs an emerging candidate that session, so a pre-gap
    "emerging" resumed after unrankable sessions never counts as a fresh emergence.
    """
    n_sessions = len(stages)
    changed = np.zeros(n_sessions, dtype=bool)
    first_emerging = np.zeros(n_sessions, dtype=bool)
    for position in range(1, n_sessions):
        changed[position] = stages[position] != stages[position - 1]
        if stages[position] == "emerging" and changed[position] and candidates[position] == "emerging":
            history = stages[max(0, position - EMERGING_REPEAT_SESSIONS): position]
            first_emerging[position] = "emerging" not in history
    return changed, first_emerging


def _theme_time_series(
    *,
    index: int,
    theme: ThemeMeta,
    basket: _Basket,
    features: _TickerFeatures,
    benchmark: np.ndarray,
    session_dates: np.ndarray,
    stacked: dict[str, np.ndarray],
    rankable: np.ndarray,
    score_price: np.ndarray,
    score_breadth: np.ndarray,
    z_att: np.ndarray,
    h_raw: np.ndarray,
    heat_now: np.ndarray,
    heat_trend: np.ndarray,
    pct_now: np.ndarray,
    pct_trend: np.ndarray,
    pct_x63: np.ndarray,
    rank_now: np.ndarray,
    proxy: dict[str, list],
    published: Optional[dict[int, dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Per-session theme series; ``published`` pins the sessions it holds (``_published_by_position``)."""
    published = published or {}
    n_sessions = len(session_dates)
    # "Previous session" values come from the published record where there is one.
    prior_rank = np.asarray(rank_now, dtype=float).copy()
    for position, row in published.items():
        if "rank_now" in row:
            prior_rank[position] = np.nan if row["rank_now"] is None else row["rank_now"]
    rank_change = np.full(n_sessions, np.nan)
    rank_change[1:] = prior_rank[:-1] - rank_now[1:]
    streak = np.zeros(n_sessions, dtype=int)
    for position in range(n_sessions):
        pinned_streak = published.get(position, {}).get("streak_top5")
        if pinned_streak is not None:
            streak[position] = max(0, pinned_streak)
        elif prior_rank[position] <= 5:
            streak[position] = (streak[position - 1] if position else 0) + 1

    ts_z = _trailing_robust_z(h_raw)
    market = benchmark / _shift(benchmark, 1) - 1.0
    residuals = _residual_returns(features.ret_clipped[:, basket.cols], market)
    cocorr = _cocorr(residuals, basket.active)
    cocorr_pct = _trailing_percentile(cocorr)
    proxy_age_days = None
    if theme.proxy_inception is not None:
        proxy_age_days = (session_dates - np.datetime64(theme.proxy_inception, "D")).astype(int)

    conditions = _stage_conditions(
        rankable=rankable,
        ts_z=ts_z,
        pct_now=pct_now,
        pct_trend=pct_trend,
        pct_x63=pct_x63,
        x21=stacked["x21"][index],
        b50=stacked["b50"][index],
        d_b50=stacked["d_b50"][index],
        ign5=stacked["ign5"][index],
        heat_now=heat_now,
        heat_trend=heat_trend,
        score_price=score_price,
        score_breadth=score_breadth,
        z_att=z_att,
        cocorr_pct=cocorr_pct,
        proxy_age_days=proxy_age_days,
    )
    candidates = conditions["candidates"]
    hold_candidates = list(candidates)
    fixed: list[Optional[str]] = [None] * n_sessions
    for position, row in published.items():
        fixed[position] = row["stage"]
        if row.get("candidate_stage") is not None:
            hold_candidates[position] = row["candidate_stage"]
    stages = apply_stage_hold(hold_candidates, fixed=fixed if published else None)
    changed, first_emerging = _stage_transitions(stages, hold_candidates)
    return {
        "rank_change": rank_change,
        "streak_top5": streak,
        "ts_z": ts_z,
        "cocorr": cocorr,
        "cocorr_pct": cocorr_pct,
        "emerge": conditions["emerge"],
        "attention_only": conditions["attention_only"],
        "candidate_stage": candidates,
        "stage": stages,
        "stage_changed": changed,
        "emerging_first_20": first_emerging,
        "proxy_etf": proxy["proxy_etf"],
        "proxy_x21": proxy["proxy_x21"],
    }


def _leader_scores(x21: np.ndarray, pth: np.ndarray, att: np.ndarray, active: np.ndarray) -> np.ndarray:
    """0.4·pct(x21) + 0.3·pct(pth) + 0.3·pct(a*) within active members; missing parts renormalize."""
    numerator = np.zeros(active.shape)
    denominator = np.zeros(active.shape)
    for values, weight in ((x21, 0.4), (pth, 0.3), (att, 0.3)):
        masked = pd.DataFrame(np.where(active, values, np.nan))
        pct = masked.rank(axis=1, method="max", pct=True).to_numpy()
        present = ~np.isnan(pct)
        numerator += np.where(present, weight * pct, 0.0)
        denominator += np.where(present, weight, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(denominator > 0, numerator / denominator, np.nan)


def _leader_ranks(scores: np.ndarray) -> np.ndarray:
    """Per row: 1 = best score, ties by member (ticker) order; NaN scores stay unranked."""
    keyed = np.where(np.isnan(scores), np.inf, -scores)
    order = np.argsort(keyed, axis=1, kind="stable")
    ranks = np.empty(scores.shape)
    np.put_along_axis(ranks, order, np.arange(1, scores.shape[1] + 1)[None, :].repeat(scores.shape[0], 0), axis=1)
    return np.where(np.isnan(scores), np.nan, ranks)


def _leaders(basket: _Basket, features: _TickerFeatures, positions: list[int]) -> dict[int, list[str]]:
    if not basket.tickers:
        return {position: [] for position in positions}
    rows = np.array(positions, dtype=int)
    cols = basket.cols
    scores = _leader_scores(
        features.x21[np.ix_(rows, cols)],
        features.pth[np.ix_(rows, cols)],
        features.att[np.ix_(rows, cols)],
        basket.active[rows],
    )
    ranks = _leader_ranks(scores)
    leaders = {}
    for offset, position in enumerate(positions):
        ranked = [
            (int(ranks[offset, member]), basket.tickers[member])
            for member in range(len(basket.tickers))
            if np.isfinite(ranks[offset, member]) and ranks[offset, member] <= LEADER_COUNT
        ]
        leaders[position] = [ticker for _, ticker in sorted(ranked)]
    return leaders


def _member_rows(
    slug: str,
    basket: _Basket,
    basket_values: dict[str, np.ndarray],
    spans: list[MemberSpan],
    features: _TickerFeatures,
    sessions: list[date],
    as_of: date,
) -> list[dict[str, Any]]:
    position = len(sessions) - 1
    active = basket.active[position]
    if not active.any():
        return []
    # Tier of the strongest interval open on as_of (the one that set the basket weight).
    tiers: dict[str, tuple[float, str]] = {}
    for span in spans:
        if span.active_on(as_of):
            best = tiers.get(span.ticker)
            if best is None or span.weight > best[0]:
                tiers[span.ticker] = (span.weight, span.tier)
    cols = basket.cols
    scores = _leader_scores(
        features.x21[position, cols][None, :],
        features.pth[position, cols][None, :],
        features.att[position, cols][None, :],
        active[None, :],
    )[0]
    ranks = _leader_ranks(scores[None, :])[0]
    weights = basket_values["weights"][position]
    first_window = max(0, len(sessions) - FIRST_IGNITION_SESSIONS)
    spark_window = max(0, len(sessions) - SPARKLINE_SESSIONS)
    rows = []
    for member, ticker in enumerate(basket.tickers):
        if not active[member]:
            continue
        column = cols[member]
        ignitions = np.flatnonzero(features.ignited[first_window:, column])
        closes = features.close[spark_window:, column]
        finite = closes[np.isfinite(closes)]
        sparkline = (
            [_float(value / finite[0], 6) for value in closes] if finite.size else [None] * len(closes)
        )
        rank = ranks[member]
        rows.append(
            {
                "theme_slug": slug,
                "ticker": ticker,
                "date": as_of,
                "tier": tiers.get(ticker, (0.0, "peripheral"))[1],
                "weight": float(weights[member]),
                "close": _float(features.close[position, column]),
                "r1": _float(features.ret[position, column]),
                "x5": _float(features.x5[position, column]),
                "x21": _float(features.x21[position, column]),
                "pth": _float(features.pth[position, column]),
                "rvol5": _float(features.rvol5[position, column]),
                "att_z": _float(features.att[position, column]),
                "ignited": bool(features.ignited[position, column]),
                "first_ignition_date": sessions[first_window + int(ignitions[0])] if ignitions.size else None,
                "leader_score": _float(scores[member]),
                "leader_rank": _int(rank),
                "is_leader": bool(np.isfinite(rank) and rank <= LEADER_COUNT),
                "sparkline": sparkline,
            }
        )
    rows.sort(key=lambda row: (row["leader_rank"] is None, row["leader_rank"] or 0, row["ticker"]))
    return rows


# ---------------------------------------------------------------------------
# Trending companies
# ---------------------------------------------------------------------------


def _company_rows(
    inputs: ScoringInputs,
    features: _TickerFeatures,
    column_of: dict[str, int],
    sessions: list[date],
    theme_slugs_by_ticker: dict[str, list[str]],
) -> tuple[list[dict[str, Any]], int]:
    tickers = sorted(str(ticker) for ticker in inputs.universe if str(ticker) in column_of)
    if not tickers:
        return [], 0
    position = len(sessions) - 1
    cols = np.array([column_of[ticker] for ticker in tickers], dtype=int)
    close = features.close[position, cols]
    window = slice(max(0, len(sessions) - COMPANY_DOLLAR_VOLUME_WINDOW), len(sessions))
    dollar_volume = features.close[window, cols] * features.volume[window, cols]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        median_dollar_volume = np.nanmedian(dollar_volume, axis=0)
    observations = (~np.isnan(dollar_volume)).sum(axis=0)
    with np.errstate(invalid="ignore"):
        eligible = (
            (close >= COMPANY_MIN_CLOSE)
            & (observations >= COMPANY_DOLLAR_VOLUME_MIN_OBS)
            & (median_dollar_volume >= COMPANY_MIN_DOLLAR_VOLUME)
        )
    if not eligible.any():
        return [], 0
    sectors = inputs.sectors or {}
    x5 = features.x5[position, cols]
    sector_values: dict[str, list[float]] = defaultdict(list)
    for member, ticker in enumerate(tickers):
        sector = sectors.get(ticker)
        if eligible[member] and sector and np.isfinite(x5[member]):
            sector_values[sector].append(float(x5[member]))
    sector_median = {sector: float(np.median(values)) for sector, values in sector_values.items()}
    x5_sector = np.array(
        [
            x5[member] - sector_median[sectors[ticker]]
            if sectors.get(ticker) in sector_median and np.isfinite(x5[member])
            else np.nan
            for member, ticker in enumerate(tickers)
        ]
    )
    components = {
        "x5_sector": x5_sector,
        "rvol5": features.rvol5[position, cols],
        "att": features.att[position, cols],
        "pth": features.pth[position, cols],
    }
    numerator = np.zeros(len(tickers))
    denominator = np.zeros(len(tickers))
    for name, weight in COMPANY_WEIGHTS:
        z = _robust_z_matrix(components[name][:, None], eligible[:, None])[:, 0]
        present = np.isfinite(z) & eligible
        numerator += np.where(present, weight * z, 0.0)
        denominator += np.where(present, weight, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        score = np.where(eligible & (denominator > 0), numerator / denominator, np.nan)
    candidates = [member for member in range(len(tickers)) if np.isfinite(score[member])]
    candidates.sort(key=lambda member: (-score[member], tickers[member]))
    names = inputs.names or {}
    industries = inputs.industries or {}
    market_caps = inputs.market_caps or {}
    rows = []
    for rank, member in enumerate(candidates[:COMPANY_TOP_N], start=1):
        ticker = tickers[member]
        column = cols[member]
        att = features.att[position, column]
        flags = []
        if np.isfinite(att) and att > COMPANY_ATTENTION_ONLY and np.isfinite(x5[member]) and x5[member] < 0:
            flags.append("attention_only")
        if features.ignited[position, column]:
            flags.append("ignited")
        rows.append(
            {
                "ticker": ticker,
                "date": sessions[position],
                "rank": rank,
                "score": float(score[member]),
                "name": names.get(ticker),
                "sector": sectors.get(ticker),
                "industry": industries.get(ticker),
                "close": _float(close[member]),
                "market_cap": _float(market_caps.get(ticker)),
                "r1": _float(features.ret[position, column]),
                "x5": _float(x5[member]),
                "x5_sector": _float(x5_sector[member]),
                "rvol5": _float(features.rvol5[position, column]),
                "att_z": _float(att),
                "pth": _float(features.pth[position, column]),
                "theme_slugs": sorted(set(theme_slugs_by_ticker.get(ticker, []))),
                "flags": flags,
            }
        )
    return rows, int(eligible.sum())


# ---------------------------------------------------------------------------
# Output conversion
# ---------------------------------------------------------------------------


def _float(value: Any, digits: Optional[int] = None) -> Optional[float]:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return round(result, digits) if digits is not None else result


def _int(value: Any) -> Optional[int]:
    result = _float(value)
    return None if result is None else int(round(result))
