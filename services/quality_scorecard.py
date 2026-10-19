"""Quality scorecard, Piotroski F-score and Altman Z from annual statements.

Everything comes from the latest stored EODHD fundamentals payload, the one the
screener already fetches: up to six fiscal years of the income statement,
balance sheet and cash-flow statement. Each metric is scored 0-100 by linear
interpolation between a fixed "weak" and "strong" threshold
(docs/quality_scorecard.md), so a stock's score never depends on which other
stocks were published that day. Like the expectation signals, these values
have no point-in-time history and are not used in backtests.
"""

from __future__ import annotations

import math
from statistics import mean
from typing import Any, Optional

TEMPLATE_STANDARD = "standard"
TEMPLATE_FINANCIAL = "financial"
TEMPLATE_REIT = "reit"
# Balance-sheet businesses: debt is raw material, so ROIC, cash conversion,
# leverage, the F-score and Altman Z do not describe their quality.
FINANCIAL_INDUSTRIES = {
    "Banks - Diversified", "Banks - Regional", "Capital Markets", "Mortgage Finance",
    "Financial Conglomerates", "Insurance - Diversified", "Insurance - Life",
    "Insurance - Property & Casualty", "Insurance - Reinsurance", "Insurance - Specialty",
    "Shell Companies",
}
MAX_YEARS = 6  # five annual changes
MIN_YEARS = 3
ROIC_HURDLE = 0.08
DEFAULT_TAX_RATE = 0.21

# (weak, strong): the score is 0 at "weak", 100 at "strong", linear between.
THRESHOLDS = {
    "roic_avg": (0.03, 0.15),
    "roic_consistency": (0.4, 1.0),
    "roe_avg": (0.06, 0.15),
    "revenue_cagr": (0.0, 0.12),
    "revenue_growth_years": (0.5, 1.0),
    "fcf_conversion": (0.5, 1.1),
    "accruals_ratio": (0.05, -0.10),
    "net_debt_to_ebitda": (4.0, 0.0),
    "interest_coverage": (2.0, 12.0),
    "share_change": (0.10, -0.05),
    "sbc_to_revenue": (0.10, 0.01),
}
METRIC_LABELS = {
    "roic_avg": ("ROIC 均值", "percent"),
    "roic_consistency": ("ROIC ≥ 8% 的年份占比", "percent"),
    "roe_avg": ("ROE 均值", "percent"),
    "revenue_cagr": ("收入复合增速", "percent"),
    "revenue_growth_years": ("收入增长年份占比", "percent"),
    "fcf_conversion": ("FCF / 净利润", "percent"),
    "accruals_ratio": ("应计比率", "percent"),
    "net_debt_to_ebitda": ("净债务 / EBITDA", "ratio"),
    "interest_coverage": ("利息覆盖倍数", "ratio"),
    "share_change": ("股本变化", "percent"),
    "sbc_to_revenue": ("股权激励 / 收入", "percent"),
}
PILLARS = (
    ("profitability", "盈利能力"),
    ("growth", "增长持续性"),
    ("cash_conversion", "现金转化"),
    ("balance_sheet", "资产负债表"),
    ("capital_allocation", "资本配置"),
)
PILLAR_METRICS = {
    TEMPLATE_STANDARD: {
        "profitability": ("roic_avg", "roic_consistency"),
        "growth": ("revenue_cagr", "revenue_growth_years"),
        "cash_conversion": ("fcf_conversion", "accruals_ratio"),
        "balance_sheet": ("net_debt_to_ebitda", "interest_coverage"),
        "capital_allocation": ("share_change", "sbc_to_revenue"),
    },
    # REIT depreciation and structural leverage distort cash conversion and debt/EBITDA.
    TEMPLATE_REIT: {
        "profitability": ("roic_avg", "roic_consistency"),
        "growth": ("revenue_cagr", "revenue_growth_years"),
        "balance_sheet": ("interest_coverage",),
        "capital_allocation": ("share_change", "sbc_to_revenue"),
    },
    TEMPLATE_FINANCIAL: {
        "profitability": ("roe_avg",),
        "growth": ("revenue_cagr", "revenue_growth_years"),
        "capital_allocation": ("share_change", "sbc_to_revenue"),
    },
}
MIN_PILLARS_FOR_SCORE = 3
GRADES = ((75, "优"), (60, "良"), (45, "中"), (0, "弱"))
ALTMAN_ZONES = ((2.99, "safe", "安全"), (1.81, "grey", "灰色"), (-math.inf, "distress", "困境"))
SCREENER_FIELDS = (
    "quality_score",
    "quality_profitability",
    "quality_growth",
    "quality_cash_conversion",
    "quality_balance_sheet",
    "quality_capital_allocation",
    "piotroski_f_score",
    "altman_z",
    "roic_5y_avg",
    "fcf_conversion_5y",
    "share_change_5y",
)


def _number(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _ratio(numerator: Optional[float], denominator: Optional[float], *, positive: bool = True) -> Optional[float]:
    if numerator is None or denominator is None or denominator == 0 or (positive and denominator < 0):
        return None
    return numerator / denominator


def _first(*values: Any) -> Optional[float]:
    return next((number for number in map(_number, values) if number is not None), None)


def template_for(industry: Optional[str]) -> str:
    name = (industry or "").strip()
    if name in FINANCIAL_INDUSTRIES:
        return TEMPLATE_FINANCIAL
    if name.startswith("REIT"):
        return TEMPLATE_REIT
    return TEMPLATE_STANDARD


def _yearly(payload: dict, statement: str) -> dict[str, dict]:
    section = (((payload.get("Financials") or {}).get(statement) or {}).get("yearly")) or {}
    rows = section.values() if isinstance(section, dict) else section if isinstance(section, list) else []
    return {str(row.get("date"))[:10]: row for row in rows if isinstance(row, dict) and row.get("date")}


def annual_rows(payload: dict) -> list[dict[str, Any]]:
    """Oldest-first fiscal years with the fields the scorecard uses."""
    income, balance, cash = (_yearly(payload, name) for name in ("Income_Statement", "Balance_Sheet", "Cash_Flow"))
    rows = []
    for day in sorted(income)[-MAX_YEARS:]:
        i, b, c = income[day], balance.get(day, {}), cash.get(day, {})
        revenue = _number(i.get("totalRevenue"))
        if revenue is None:
            continue
        ebit = _first(i.get("ebit"), i.get("operatingIncome"))
        pretax, tax = _number(i.get("incomeBeforeTax")), _number(i.get("incomeTaxExpense"))
        if pretax is not None and pretax <= 0:
            tax_rate = 0.0
        else:
            rate = _ratio(tax, pretax)
            tax_rate = rate if rate is not None and 0 <= rate <= 0.5 else DEFAULT_TAX_RATE
        equity = _number(b.get("totalStockholderEquity"))
        total_debt = _first(b.get("shortLongTermDebtTotal"), b.get("longTermDebtTotal"), b.get("longTermDebt"))
        cfo = _number(c.get("totalCashFromOperatingActivities"))
        capex = _number(c.get("capitalExpenditures"))
        rows.append({
            "date": day,
            "revenue": revenue,
            "gross_profit": _number(i.get("grossProfit")),
            "operating_income": _number(i.get("operatingIncome")),
            "ebit": ebit,
            "ebitda": _first(i.get("ebitda")) if _number(i.get("ebitda")) is not None else (
                ebit + _number(c.get("depreciation")) if ebit is not None and _number(c.get("depreciation")) is not None else None),
            "net_income": _number(i.get("netIncome")),
            "interest_expense": abs(value) if (value := _number(i.get("interestExpense"))) is not None else None,
            "tax_rate": tax_rate,
            "total_assets": _number(b.get("totalAssets")),
            "total_liabilities": _number(b.get("totalLiab")),
            "current_assets": _number(b.get("totalCurrentAssets")),
            "current_liabilities": _number(b.get("totalCurrentLiabilities")),
            "retained_earnings": _number(b.get("retainedEarnings")),
            "equity": equity,
            "long_term_debt": _first(b.get("longTermDebtTotal"), b.get("longTermDebt")),
            "total_debt": total_debt,
            "cash": _first(b.get("cashAndShortTermInvestments"), b.get("cashAndEquivalents"), b.get("cash")),
            "shares": _number(b.get("commonStockSharesOutstanding")),
            "invested_capital": _first(b.get("netInvestedCapital")) or (
                equity + total_debt if equity is not None and total_debt is not None else None),
            "cfo": cfo,
            "fcf": _first(c.get("freeCashFlow")) if _number(c.get("freeCashFlow")) is not None else (
                cfo - abs(capex) if cfo is not None and capex is not None else None),
            "sbc": _number(c.get("stockBasedCompensation")),
        })
    for row in rows:
        row["roic"] = _ratio(row["ebit"] * (1 - row["tax_rate"]) if row["ebit"] is not None else None, row["invested_capital"])
        row["roe"] = _ratio(row["net_income"], row["equity"])
        row["operating_margin"] = _ratio(row["operating_income"], row["revenue"])
        row["gross_margin"] = _ratio(row["gross_profit"], row["revenue"])
    return rows


def _scale(key: str, value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    weak, strong = THRESHOLDS[key]
    position = (value - weak) / (strong - weak)
    return round(100 * min(max(position, 0.0), 1.0), 1)


def _metrics(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest = rows[-1]
    previous = rows[-2] if len(rows) > 1 else None
    enough = len(rows) >= MIN_YEARS
    roics = [row["roic"] for row in rows if row["roic"] is not None]
    roes = [row["roe"] for row in rows if row["roe"] is not None]
    revenues = [row["revenue"] for row in rows]
    changes = [later / earlier - 1 for earlier, later in zip(revenues, revenues[1:]) if earlier > 0]
    net_incomes = [row["net_income"] for row in rows]
    fcfs = [row["fcf"] for row in rows]
    values: dict[str, Optional[float]] = {
        "roic_avg": mean(roics) if len(roics) >= MIN_YEARS else None,
        "roic_consistency": sum(value >= ROIC_HURDLE for value in roics) / len(roics) if len(roics) >= MIN_YEARS else None,
        "roe_avg": mean(roes) if len(roes) >= MIN_YEARS else None,
        "revenue_cagr": (
            (revenues[-1] / revenues[0]) ** (1 / (len(revenues) - 1)) - 1
            if enough and revenues[0] > 0 and revenues[-1] > 0 else None
        ),
        "revenue_growth_years": sum(change > 0 for change in changes) / len(changes) if len(changes) >= MIN_YEARS - 1 else None,
        "fcf_conversion": None,
        "accruals_ratio": None,
        "net_debt_to_ebitda": None,
        "interest_coverage": None,
        "share_change": None,
        "sbc_to_revenue": _ratio(latest["sbc"], latest["revenue"]),
    }
    if enough and all(value is not None for value in net_incomes + fcfs) and sum(net_incomes) > 0:
        values["fcf_conversion"] = sum(fcfs) / sum(net_incomes)
    if previous and None not in (latest["net_income"], latest["cfo"], latest["total_assets"], previous["total_assets"]):
        values["accruals_ratio"] = _ratio(latest["net_income"] - latest["cfo"], (latest["total_assets"] + previous["total_assets"]) / 2)
    scores: dict[str, Optional[float]] = {}
    net_debt = latest["total_debt"] - latest["cash"] if None not in (latest["total_debt"], latest["cash"]) else None
    if net_debt is not None and latest["ebitda"] is not None:
        if net_debt <= 0:
            values["net_debt_to_ebitda"] = _ratio(net_debt, latest["ebitda"]) if latest["ebitda"] > 0 else None
            scores["net_debt_to_ebitda"] = 100.0  # Net cash.
        elif latest["ebitda"] > 0:
            values["net_debt_to_ebitda"] = net_debt / latest["ebitda"]
        else:
            scores["net_debt_to_ebitda"] = 0.0  # Net debt with no operating earnings to carry it.
    if latest["total_debt"] == 0:
        scores["interest_coverage"] = 100.0  # No borrowings to service.
    elif latest["interest_expense"] and latest["ebit"] is not None:
        values["interest_coverage"] = latest["ebit"] / latest["interest_expense"]
    shares = [row["shares"] for row in rows if row["shares"]]
    if enough and len(shares) >= MIN_YEARS:
        values["share_change"] = shares[-1] / shares[0] - 1
    metrics = {}
    for key, value in values.items():
        score = scores.get(key, _scale(key, value))
        label, unit = METRIC_LABELS[key]
        metrics[key] = {"value": value, "score": score, "label": label, "unit": unit,
                        "weak": THRESHOLDS[key][0], "strong": THRESHOLDS[key][1]}
    return metrics


def piotroski(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The nine Piotroski checks, latest fiscal year against the one before."""
    if len(rows) < 2:
        return {"score": None, "available": 0, "checks": []}
    t, p = rows[-1], rows[-2]

    def roa(row):
        return _ratio(row["net_income"], row["total_assets"])

    def leverage(row):
        return _ratio(row["long_term_debt"] or 0.0, row["total_assets"]) if row["long_term_debt"] is not None else None

    def current_ratio(row):
        return _ratio(row["current_assets"], row["current_liabilities"])

    def turnover(row):
        return _ratio(row["revenue"], row["total_assets"])

    def compare(now, before, better):
        return None if now is None or before is None else better(now, before)

    checks = [
        ("roa_positive", "资产收益率为正", None if roa(t) is None else roa(t) > 0),
        ("cfo_positive", "经营现金流为正", None if t["cfo"] is None else t["cfo"] > 0),
        ("roa_improving", "资产收益率上升", compare(roa(t), roa(p), lambda a, b: a > b)),
        ("cash_above_earnings", "经营现金流高于净利润", None if None in (t["cfo"], t["net_income"]) else t["cfo"] > t["net_income"]),
        ("leverage_falling", "长期负债率未上升", compare(leverage(t), leverage(p), lambda a, b: a <= b)),
        ("liquidity_improving", "流动比率上升", compare(current_ratio(t), current_ratio(p), lambda a, b: a > b)),
        ("no_dilution", "未增发股份", compare(t["shares"], p["shares"], lambda a, b: a <= b)),
        ("margin_improving", "毛利率上升", compare(t["gross_margin"], p["gross_margin"], lambda a, b: a > b)),
        ("turnover_improving", "资产周转率上升", compare(turnover(t), turnover(p), lambda a, b: a > b)),
    ]
    available = [passed for _, _, passed in checks if passed is not None]
    return {
        # A partial count would read as a weak score, so the score needs all nine.
        "score": sum(available) if len(available) == len(checks) else None,
        "available": len(available),
        "checks": [{"key": key, "label": label, "passed": passed} for key, label, passed in checks],
        "fiscal_years": [p["date"], t["date"]],
    }


def altman_z(row: dict[str, Any], market_cap: Optional[float]) -> Optional[dict[str, Any]]:
    """Original Altman Z with market equity; zones 1.81 / 2.99."""
    assets, liabilities = row["total_assets"], row["total_liabilities"]
    inputs = (row["current_assets"], row["current_liabilities"], row["retained_earnings"], row["ebit"], market_cap, assets, liabilities)
    if any(value is None for value in inputs) or not assets or assets <= 0 or not liabilities or liabilities <= 0:
        return None
    components = {
        "working_capital": (row["current_assets"] - row["current_liabilities"]) / assets,
        "retained_earnings": row["retained_earnings"] / assets,
        "ebit": row["ebit"] / assets,
        "market_equity": market_cap / liabilities,
        "sales": row["revenue"] / assets,
    }
    z = (1.2 * components["working_capital"] + 1.4 * components["retained_earnings"] + 3.3 * components["ebit"]
         + 0.6 * components["market_equity"] + 1.0 * components["sales"])
    zone, label = next((zone, label) for floor, zone, label in ALTMAN_ZONES if z > floor)
    return {"z": round(z, 2), "zone": zone, "zone_label": label,
            "components": {key: round(value, 4) for key, value in components.items()}, "fiscal_year": row["date"]}


def build_quality_scorecard(payload: dict) -> dict[str, Any]:
    general = payload.get("General") or {}
    template = template_for(general.get("Industry"))
    rows = annual_rows(payload)
    card: dict[str, Any] = {
        "template": template, "industry": general.get("Industry"), "available": False,
        "score": None, "grade": None, "pillars": [], "metrics": {}, "piotroski": None, "altman": None,
        "fiscal_years": [row["date"] for row in rows], "series": {}, "notes": [],
    }
    if template == TEMPLATE_FINANCIAL:
        card["notes"].append("银行、保险和资本市场公司用 ROE 衡量盈利能力，不计算现金转化、资产负债表、F 分数和 Altman Z。")
    elif template == TEMPLATE_REIT:
        card["notes"].append("REIT 的折旧和结构性负债会扭曲现金转化与净债务/EBITDA，资产负债表只看利息覆盖，不计算 Altman Z。")
    if len(rows) < 2:
        card["notes"].append("年报少于两年，无法评分。")
        return card
    if len(rows) < MIN_YEARS:
        card["notes"].append(f"只有 {len(rows)} 个财年，多年指标和综合分需要至少 {MIN_YEARS} 年。")
    metrics = _metrics(rows)
    pillars = []
    for key, label in PILLARS:
        names = PILLAR_METRICS[template].get(key)
        if names is None:
            pillars.append({"key": key, "label": label, "score": None, "metrics": [], "applicable": False})
            continue
        scores = [metrics[name]["score"] for name in names if metrics[name]["score"] is not None]
        pillars.append({"key": key, "label": label, "score": round(mean(scores), 1) if scores else None,
                        "metrics": list(names), "applicable": True})
    scored = [pillar["score"] for pillar in pillars if pillar["score"] is not None]
    # Single-year metrics alone could fill three pillars; a score needs the multi-year view.
    score = round(mean(scored)) if len(scored) >= MIN_PILLARS_FOR_SCORE and len(rows) >= MIN_YEARS else None
    card.update({
        "available": score is not None,
        "score": score,
        "grade": next(label for floor, label in GRADES if score >= floor) if score is not None else None,
        "pillars": pillars,
        "metrics": {key: value for key, value in metrics.items()
                    if any(key in pillar["metrics"] for pillar in pillars)},
        "series": {key: [row[key] for row in rows]
                   for key in ("revenue", "operating_margin", "roic", "roe", "net_income", "fcf", "shares")},
    })
    if score is None and len(rows) >= MIN_YEARS:
        card["notes"].append(f"有数据的支柱少于 {MIN_PILLARS_FOR_SCORE} 项，不给综合分。")
    if template != TEMPLATE_FINANCIAL:
        card["piotroski"] = piotroski(rows)
    if template == TEMPLATE_STANDARD:
        card["altman"] = altman_z(rows[-1], _number((payload.get("Highlights") or {}).get("MarketCapitalization")))
    return card


def screener_values(payload: dict) -> dict[str, Optional[float]]:
    """Screener columns; every field is present so a refresh clears stale values."""
    card = build_quality_scorecard(payload)
    pillars = {pillar["key"]: pillar["score"] for pillar in card["pillars"]}
    metrics = card["metrics"]
    return {
        "quality_score": card["score"],
        "quality_profitability": pillars.get("profitability"),
        "quality_growth": pillars.get("growth"),
        "quality_cash_conversion": pillars.get("cash_conversion"),
        "quality_balance_sheet": pillars.get("balance_sheet"),
        "quality_capital_allocation": pillars.get("capital_allocation"),
        "piotroski_f_score": (card["piotroski"] or {}).get("score"),
        "altman_z": (card["altman"] or {}).get("z"),
        "roic_5y_avg": (metrics.get("roic_avg") or {}).get("value"),
        "fcf_conversion_5y": (metrics.get("fcf_conversion") or {}).get("value"),
        "share_change_5y": (metrics.get("share_change") or {}).get("value"),
    }


async def get_quality_scorecard(ticker: str, db: Any) -> dict[str, Any]:
    """The scorecard from the ticker's latest stored fundamentals payload."""
    # Imported here: events_expectations imports the expectation module chain.
    from services.events_expectations import load_latest_fundamentals_snapshot

    snapshot, payload = await load_latest_fundamentals_snapshot(ticker, db)
    if snapshot is None or payload is None:
        return {
            "ticker": ticker, "available": False, "template": None, "score": None, "grade": None,
            "pillars": [], "metrics": {}, "piotroski": None, "altman": None, "fiscal_years": [], "series": {},
            "source": "EODHD fundamentals", "fetched_at": None,
            "notes": ["No fundamentals snapshot is stored for this ticker." if snapshot is None
                      else "The latest fundamentals snapshot could not be read."],
        }
    fetched = getattr(snapshot, "fetched_at", None)
    return {"ticker": ticker, "source": "EODHD fundamentals",
            "fetched_at": fetched.isoformat() if fetched else None, **build_quality_scorecard(payload)}
