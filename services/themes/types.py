"""Shared value types passed between theme jobs (pure data, no I/O)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

import pandas as pd


STAGES = ("emerging", "accelerating", "leading", "crowded", "fading", "neutral", "insufficient")


@dataclass(frozen=True)
class MemberSpan:
    """A membership interval; ``valid_to`` is exclusive (None = open)."""

    theme_slug: str
    ticker: str
    tier: str  # "core" | "peripheral"
    weight: float  # tier weight before capping (1.0 / 0.75 / 0.4)
    valid_from: date
    valid_to: Optional[date] = None

    def active_on(self, session: date) -> bool:
        return self.valid_from <= session and (self.valid_to is None or session < self.valid_to)


@dataclass(frozen=True)
class ThemeMeta:
    slug: str
    proxy_etfs: tuple[str, ...] = ()
    # Latest inception date among the theme's proxy ETFs (crowding signal).
    proxy_inception: Optional[date] = None


@dataclass
class ScoringInputs:
    """Everything ``scoring.score_themes`` needs; built by ``pipeline`` from local tables.

    Frames are indexed by ascending ``datetime.date`` and use canonical tickers
    ("NVDA.US") as columns. Missing observations are NaN, never zero.
    """

    as_of: date
    sessions: list[date]
    close: pd.DataFrame  # sessions x tickers, adjusted close (raw close fallback)
    volume: pd.DataFrame  # sessions x tickers, share volume
    benchmark: pd.Series  # sessions -> SPY adjusted close
    members: list[MemberSpan]
    themes: list[ThemeMeta]
    industries: dict[str, str] = field(default_factory=dict)
    sectors: dict[str, str] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    market_caps: dict[str, float] = field(default_factory=dict)
    # Tickers eligible for the trending-companies list (screener universe).
    universe: frozenset[str] = frozenset()
    # UTC news days x tickers. Complete days have zeros filled; days that were
    # not ingested are entire NaN rows. ``attention_totals`` is NaN for them too.
    attention_counts: pd.DataFrame = field(default_factory=pd.DataFrame)
    attention_totals: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    proxy_close: pd.DataFrame = field(default_factory=pd.DataFrame)  # sessions x proxy ETFs
    # Emit theme rows for this many trailing sessions (1 = as_of only; backfill > 1).
    output_sessions: int = 1


@dataclass
class ScoringResult:
    """Rows ready to insert; keys match the ORM column names plus ``theme_slug``.

    ``theme_rows``: ThemeDaily columns except id/source_run_id/theme_id/scoring_version.
    ``member_rows``: ThemeMemberDaily columns except id/source_run_id/theme_id (as_of only).
    ``company_rows``: CompanyTrendDaily columns except id/source_run_id (as_of only, top N).
    """

    theme_rows: list[dict[str, Any]]
    member_rows: list[dict[str, Any]]
    company_rows: list[dict[str, Any]]
    diagnostics: dict[str, Any]
