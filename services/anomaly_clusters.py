"""Group a scan's large movers by theme or industry.

On 2026-10-16 the ten largest moves were all AI data-centre, hardware and
software names falling 10-12% on the same financing worries, yet each was
reported alone with "cause unconfirmed". When several movers share a theme
(or, failing that, an industry) and move the same way, the report names the
group before the individual stocks. Grouping uses the theme members and the
latest Screener industries already stored; it never infers a cause.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import date
from typing import Any, Iterable, Optional

from sqlalchemy import func, or_, select

from database import async_session_maker
from models import StockScreenerSnapshot, Theme, ThemeMember, Ticker
from services.report_market import number

logger = logging.getLogger(__name__)
MIN_CLUSTER_SIZE = 3


def _canonical(ticker: Any) -> str:
    code = str(ticker or "").strip().upper()
    return code if not code or "." in code else f"{code}.US"


def _cluster(
    kind: str,
    name: str,
    slug: Optional[str],
    direction: str,
    members: Iterable[str],
    moves: dict[str, float],
) -> dict[str, Any]:
    # Largest move first in the group's direction.
    ordered = sorted(members, key=lambda ticker: moves[ticker], reverse=direction == "up")
    average = sum(moves[ticker] for ticker in ordered) / len(ordered)
    return {
        "kind": kind,
        "name": name,
        "slug": slug,
        "direction": direction,
        "tickers": ordered,
        "average_move": round(average, 2),
    }


def cluster_anomalies(
    moves: dict[str, float],
    themes: dict[str, list[tuple[str, str]]],
    industries: dict[str, str],
) -> list[dict[str, Any]]:
    """Same-direction groups of at least ``MIN_CLUSTER_SIZE`` movers.

    ``themes`` maps a ticker to its (slug, name) themes; ``industries`` maps it
    to the Screener industry. The theme covering the most remaining movers is
    taken first, so one stock lands in one group; leftovers try industries.
    """
    clusters: list[dict[str, Any]] = []
    for direction in ("down", "up"):
        pool = {
            ticker for ticker, move in moves.items()
            if move != 0 and (move < 0) == (direction == "down")
        }
        while pool:
            counts = Counter(theme for ticker in pool for theme in themes.get(ticker, ()))
            if not counts:
                break
            theme, size = max(counts.items(), key=lambda item: (item[1], item[0]))
            if size < MIN_CLUSTER_SIZE:
                break
            members = {ticker for ticker in pool if theme in themes.get(ticker, ())}
            clusters.append(_cluster("theme", theme[1], theme[0], direction, members, moves))
            pool -= members
        industry_counts = Counter(industries[ticker] for ticker in pool if industries.get(ticker))
        for industry, size in sorted(industry_counts.items(), key=lambda item: (-item[1], item[0])):
            if size < MIN_CLUSTER_SIZE:
                continue
            members = {ticker for ticker in pool if industries.get(ticker) == industry}
            clusters.append(_cluster("industry", industry, None, direction, members, moves))
            pool -= members
    clusters.sort(key=lambda item: (-len(item["tickers"]), item["name"]))
    return clusters


async def _theme_memberships(tickers: list[str], as_of: date) -> dict[str, list[tuple[str, str]]]:
    async with async_session_maker() as db:
        rows = (await db.execute(
            select(ThemeMember.ticker, Theme.slug, Theme.name_zh, Theme.name_en)
            .join(Theme, Theme.id == ThemeMember.theme_id)
            .where(
                Theme.status != "retired",
                ThemeMember.ticker.in_(tickers),
                ThemeMember.valid_from <= as_of,
                or_(ThemeMember.valid_to.is_(None), ThemeMember.valid_to > as_of),
            )
        )).all()
    memberships: dict[str, list[tuple[str, str]]] = {}
    for ticker, slug, name_zh, name_en in rows:
        theme = (slug, name_zh or name_en or slug)
        if theme not in memberships.setdefault(ticker, []):
            memberships[ticker].append(theme)
    return memberships


async def _industries(tickers: list[str], as_of: date) -> dict[str, str]:
    async with async_session_maker() as db:
        latest = await db.scalar(
            select(func.max(StockScreenerSnapshot.date)).where(StockScreenerSnapshot.date <= as_of)
        )
        industries: dict[str, str] = {}
        if latest is not None:
            for ticker, industry in (await db.execute(
                select(StockScreenerSnapshot.ticker, StockScreenerSnapshot.industry).where(
                    StockScreenerSnapshot.date == latest,
                    StockScreenerSnapshot.ticker.in_(tickers),
                )
            )).all():
                if industry:
                    industries[ticker] = industry
        missing = [ticker for ticker in tickers if ticker not in industries]
        if missing:
            for ticker, industry in (await db.execute(
                select(Ticker.ticker, Ticker.industry).where(Ticker.ticker.in_(missing))
            )).all():
                if industry:
                    industries[ticker] = industry
    return industries


async def collect_anomaly_clusters(anomalies: list[dict[str, Any]], as_of: date) -> dict[str, Any]:
    moves = {
        _canonical(item.get("ticker")): float(item["price_change"])
        for item in anomalies
        if _canonical(item.get("ticker")) and number(item.get("price_change")) is not None
    }
    evidence: dict[str, Any] = {"as_of": as_of.isoformat(), "movers": len(moves), "clusters": []}
    if len(moves) < MIN_CLUSTER_SIZE:
        return evidence
    tickers = sorted(moves)
    evidence["clusters"] = cluster_anomalies(
        moves,
        await _theme_memberships(tickers, as_of),
        await _industries(tickers, as_of),
    )
    return evidence
