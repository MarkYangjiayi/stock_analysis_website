"""Rule-based market regime for the Feishu reports (no LLM).

Seven readings, each with a state, a colour and the number behind it:
trend, breadth, participation, volatility, credit, the yield curve and
valuation. Inputs come from data the reports already rely on (the published
market overview, the report's own quotes and Treasury rates, the published
index P/E) plus FRED's high-yield spread. The overview publishes overnight, so
a report taken after the close extends trend and participation with its own
SPY and RSP closing quotes. A reading whose input is missing is left out and
named, never guessed. The result is stored with the report evidence, so a
saved report always renders the same line.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Optional

from database import async_session_maker
from services.credit_spreads import CreditSpreadUnavailable, get_hy_oas
from services.decision_support import midrank_percentile
from services.index_valuation import IndexValuationUnavailable, get_index_valuation
from services.market_breadth import RSP_TICKER, get_market_overview
from services.report_market import number
from services.rrg_prices import RRG_BENCHMARK

logger = logging.getLogger(__name__)

# Feishu font colours: benign, stressed, caution, neutral.
GOOD, BAD, WARN, NEUTRAL = "green", "red", "orange", "grey"
LABELS = {
    "trend": "趋势",
    "breadth": "宽度",
    "participation": "参与度",
    "volatility": "波动",
    "credit": "信用",
    "curve": "利率曲线",
    "valuation": "估值",
}
ORDER = tuple(LABELS)
BREADTH_STRONG, BREADTH_WEAK = 60.0, 40.0
PARTICIPATION_BAND = 0.01
VIX_CALM, VIX_NORMAL, VIX_FEAR = 15.0, 20.0, 30.0
HY_OAS_WIDE, HY_OAS_STRESS = 4.5, 6.0
HY_OAS_WIDENING, HY_OAS_SHOCK = 0.4, 1.0
CURVE_FLAT_BP = 50.0
VALUATION_RICH, VALUATION_CHEAP = 80.0, 20.0


def _item(key: str, state: str, tone: str, detail: str, as_of: Optional[str]) -> dict[str, Any]:
    return {"key": key, "label": LABELS[key], "state": state, "tone": tone, "detail": detail, "as_of": as_of}


def _finite(values: list[Any]) -> list[float]:
    return [float(value) for value in values if number(value) is not None]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def trend_reading(overview: dict) -> Optional[dict[str, Any]]:
    closes = _finite((overview.get("benchmark") or {}).get("absolute_index") or [])
    if len(closes) < 200:
        return None
    last, ma50, ma200 = closes[-1], _mean(closes[-50:]), _mean(closes[-200:])
    if last > ma50 > ma200:
        state, tone = "上升", GOOD
    elif last < ma50 < ma200:
        state, tone = "下降", BAD
    else:
        state, tone = "震荡", NEUTRAL
    return _item("trend", state, tone, f"SPY 距 200 日线 {last / ma200 - 1:+.1%}", (overview.get("meta") or {}).get("as_of_date"))


def breadth_reading(overview: dict) -> Optional[dict[str, Any]]:
    series = (overview.get("breadth") or {}).get("pct_above_ma200") or []
    if not series or number(series[-1]) is None:
        return None
    level = float(series[-1])
    week_ago = number(series[-6]) if len(series) >= 6 else None
    if level >= BREADTH_STRONG:
        state, tone = "健康", GOOD
    elif level < BREADTH_WEAK:
        state, tone = "偏弱", BAD
    else:
        state, tone = "中性", NEUTRAL
    change = f"，一周 {level - week_ago:+.0f}pt" if week_ago is not None else ""
    return _item("breadth", state, tone, f"站上 200 日线 {level:.0f}%{change}", (overview.get("meta") or {}).get("as_of_date"))


def participation_reading(overview: dict) -> Optional[dict[str, Any]]:
    """Equal weight against cap weight: how widely gains or losses are shared, not their direction."""
    ratio = _finite(overview.get("rsp_spy_index") or [])
    if len(ratio) < 22 or ratio[-22] <= 0:
        return None
    change = ratio[-1] / ratio[-22] - 1
    if change >= PARTICIPATION_BAND:
        state, tone = "广泛", GOOD
    elif change <= -PARTICIPATION_BAND:
        state, tone = "集中", WARN
    else:
        state, tone = "均衡", NEUTRAL
    verb = "跑赢" if change >= 0 else "跑输"
    return _item("participation", state, tone, f"等权{verb}市值加权 21 日 {change:+.1%}", (overview.get("meta") or {}).get("as_of_date"))


def with_session_close(overview: dict, quotes: list[dict]) -> Optional[dict]:
    """The overview with SPY and RSP/SPY extended by the report's closing quotes.

    Only quotes taken after the regular close of the session that follows the
    overview's last date qualify, so a morning report and a stale overview stay
    as published. Breadth needs every member's close and is not extended. The
    quotes are raw prices, so on an ex-dividend day the move includes the
    payout until the overnight publication replaces it.
    """
    rows = {row.get("ticker"): row for row in quotes}
    dates = overview.get("dates") or []
    moves = []
    for ticker in (RRG_BENCHMARK, RSP_TICKER):
        row = rows.get(ticker) or {}
        session = row.get("session") or {}
        price, previous = number(row.get("price")), number(row.get("previous_close"))
        if (
            row.get("status") != "available" or session.get("state") != "已收盘"
            or row.get("session_date") != session.get("expected_date")
            or not dates or session.get("previous_date") != dates[-1]
            or not price or not previous or price <= 0 or previous <= 0
        ):
            return None
        moves.append(price / previous)
    spy_move, rsp_move = moves
    benchmark = (overview.get("benchmark") or {}).get("absolute_index") or []
    ratio = overview.get("rsp_spy_index") or []
    if not benchmark or not ratio or number(benchmark[-1]) is None or number(ratio[-1]) is None:
        return None
    session_date = rows[RRG_BENCHMARK]["session_date"]
    return {
        **overview,
        "meta": {**(overview.get("meta") or {}), "as_of_date": session_date},
        "dates": [*dates, session_date],
        "benchmark": {**overview["benchmark"], "absolute_index": [*benchmark, benchmark[-1] * spy_move]},
        "rsp_spy_index": [*ratio, ratio[-1] * rsp_move / spy_move],
    }


def volatility_reading(vix: Optional[float], as_of: Optional[str]) -> Optional[dict[str, Any]]:
    if vix is None or not math.isfinite(vix) or vix <= 0:
        return None
    if vix < VIX_CALM:
        state, tone = "平静", GOOD
    elif vix < VIX_NORMAL:
        state, tone = "正常", NEUTRAL
    elif vix < VIX_FEAR:
        state, tone = "偏高", WARN
    else:
        state, tone = "恐慌", BAD
    return _item("volatility", state, tone, f"VIX {vix:.1f}", as_of)


def credit_reading(hy_oas: Optional[dict]) -> Optional[dict[str, Any]]:
    level = number((hy_oas or {}).get("value_pct"))
    if level is None:
        return None
    change = number(hy_oas.get("change_1m_pt"))
    if level >= HY_OAS_STRESS or (change is not None and change >= HY_OAS_SHOCK):
        state, tone = "紧张", BAD
    elif level >= HY_OAS_WIDE or (change is not None and change >= HY_OAS_WIDENING):
        state, tone = "走阔", WARN
    else:
        state, tone = "平稳", GOOD
    parts = [f"高收益债利差 {level:.2f}%"]
    if change is not None:
        parts.append(f"一月 {change:+.2f}pt")
    if number(hy_oas.get("percentile")) is not None:
        parts.append(f"3 年 {hy_oas['percentile']:.0f} 分位")
    return _item("credit", state, tone, "，".join(parts), hy_oas.get("as_of"))


def curve_reading(treasury: Optional[dict]) -> Optional[dict[str, Any]]:
    spread = number((treasury or {}).get("spread_10y_2y_bp"))
    if spread is None:
        return None
    if spread < 0:
        state, tone = "倒挂", WARN
    elif spread < CURVE_FLAT_BP:
        state, tone = "偏平", NEUTRAL
    else:
        state, tone = "正常", GOOD
    return _item("curve", state, tone, f"10Y−2Y {spread:+.0f}bp", treasury.get("as_of"))


def valuation_reading(valuation: Optional[dict]) -> Optional[dict[str, Any]]:
    percentile = number((valuation or {}).get("percentile"))
    pe = number((valuation or {}).get("index_pe"))
    if percentile is None or pe is None:
        return None
    if percentile >= VALUATION_RICH:
        state, tone = "偏贵", WARN
    elif percentile <= VALUATION_CHEAP:
        state, tone = "便宜", GOOD
    else:
        state, tone = "中性", NEUTRAL
    return _item(
        "valuation", state, tone,
        f"标普 500 市盈率 {pe:.1f}，{valuation.get('months')} 个月中 {percentile:.0f} 分位",
        valuation.get("as_of"),
    )


def build_regime(
    *,
    overview: Optional[dict],
    vix: Optional[float],
    vix_as_of: Optional[str],
    hy_oas: Optional[dict],
    treasury: Optional[dict],
    valuation: Optional[dict],
    closing_overview: Optional[dict] = None,
) -> dict[str, Any]:
    prices = closing_overview or overview
    readings = {
        "trend": trend_reading(prices) if prices else None,
        "breadth": breadth_reading(overview) if overview else None,
        "participation": participation_reading(prices) if prices else None,
        "volatility": volatility_reading(vix, vix_as_of),
        "credit": credit_reading(hy_oas),
        "curve": curve_reading(treasury),
        "valuation": valuation_reading(valuation),
    }
    return {
        "schema_version": 1,
        "items": [readings[key] for key in ORDER if readings[key] is not None],
        "unavailable": [LABELS[key] for key in ORDER if readings[key] is None],
        "overview_stale": bool(((overview or {}).get("meta") or {}).get("stale")),
    }


async def valuation_summary() -> Optional[dict[str, Any]]:
    """Latest published S&P 500 month-end P/E and its percentile in the series."""
    try:
        async with async_session_maker() as db:
            payload = await get_index_valuation(db, "SP500")
    except IndexValuationUnavailable:
        return None
    points = [point for point in payload.get("points", []) if number(point.get("index_pe")) is not None]
    if not points:
        return None
    latest = points[-1]
    values = [point["index_pe"] for point in points]
    return {
        "as_of": latest["date"], "index_pe": latest["index_pe"], "months": len(values),
        "percentile": round(midrank_percentile(latest["index_pe"], values), 1),
        "stale": bool(payload.get("meta", {}).get("stale")),
    }


async def collect_market_regime(context: dict, now: datetime) -> dict[str, Any]:
    """Gather the inputs and build the regime; every source fails independently."""
    warnings: list[str] = []
    overview = None
    try:
        async with async_session_maker() as db:
            overview = await get_market_overview(db, "SP500", "1y")
    except Exception as exc:
        logger.warning("Regime: market overview unavailable (%s)", type(exc).__name__)
        warnings.append("市场总览")
    hy_oas = None
    try:
        hy_oas = await get_hy_oas(now=now)
    except (CreditSpreadUnavailable, OSError) as exc:
        logger.warning("Regime: high-yield spread unavailable (%s)", type(exc).__name__)
        warnings.append("高收益债利差")
    valuation = None
    try:
        valuation = await valuation_summary()
    except Exception as exc:
        logger.warning("Regime: index valuation unavailable (%s)", type(exc).__name__)
    vix_row = next((row for row in context.get("quotes", []) if row.get("ticker") == "VIX.INDX"), None) or {}
    vix_as_of = vix_row.get("session_date") or (str(vix_row.get("as_of") or "")[:10] or None)
    regime = build_regime(
        overview=overview,
        vix=number(vix_row.get("price")),
        vix_as_of=vix_as_of,
        hy_oas=hy_oas,
        treasury=context.get("treasury"),
        valuation=valuation,
        closing_overview=with_session_close(overview, context.get("quotes", [])) if overview else None,
    )
    if hy_oas is not None:
        regime["credit_source"] = {"series": hy_oas["series"], "url": hy_oas["source_url"], "from_cache": hy_oas.get("from_cache_after_failure", False)}
    regime["fetch_warnings"] = warnings
    return regime
