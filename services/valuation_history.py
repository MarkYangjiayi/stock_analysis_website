"""Reconstructed historical multiples, separate from published PIT factor data.

EODHD statement shares are a provider proxy, typically split-adjusted to the
payload's vintage. They are not an exact historical market-cap series. We use
that vintage (never the fiscal date) to put shares and raw prices on one split
basis. No dividend adjustments, current valuation snapshots or non-GAAP
Earnings.History EPS enter these calculations.
"""
from __future__ import annotations

import calendar
import math
from bisect import bisect_right
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import CorporateAction, DailyPrice, FinancialStatement, FundamentalVersion, Ticker
from services.events_expectations import load_latest_fundamentals_snapshot
from services.valuation_inputs import field, number, resolve_debt
from services.valuation_history_quality import statement_quality
from services.split_history import UNVERIFIED_REASON, load_split_history


METRICS = (
    ("pe", "P/E", "TTM · reported earnings estimate", "Split-only price / sum of four quarterly reported earnings per provider share"),
    ("ps", "P/S", "TTM · equity value estimate", "Equity value proxy / TTM revenue"),
    ("pb", "P/B", "Latest quarter · equity value estimate", "Equity value proxy / latest stockholders' equity"),
    ("pfcf", "P/FCF", "TTM · CFO less capex", "Equity value proxy / TTM (operating cash flow − absolute capex)"),
    ("ev_revenue", "EV/Revenue", "TTM · simplified EV estimate", "(Equity value proxy + debt − cash and short-term investments) / TTM revenue"),
    ("ev_ebitda", "EV/EBITDA", "TTM · simplified EV estimate", "(Equity value proxy + debt − cash and short-term investments) / TTM EBITDA"),
)
KEYS = tuple(row[0] for row in METRICS)
METHODOLOGY = [
    "Reconstructed history, not a point-in-time backtest dataset: initial provider payloads may contain later restatements. Recorded revisions become effective only after their availability date.",
    "Statements become effective on the first price session after the reported filing date and recorded availability. Missing, estimated and fiscal-end placeholder filing dates are excluded. Quarterly inputs expire 180 days after period end.",
    "Price and provider statement shares use the same split basis, without dividend adjustments. Provider shares may be weighted averages; equity value and P/E are estimates, not exact historical market cap or verified GAAP diluted P/E.",
    "P/E uses reported net income per quarterly provider share, never the non-GAAP Earnings.History EPS. TTM metrics require four consecutive quarters; missing or non-positive denominators remain gaps.",
    "Conflicting financial inputs and quarterly totals that disagree with an already disclosed annual statement remain gaps. Annual checks never backdate later filings or revisions.",
    "EV is a simplified equity + debt − cash/short-term-investments estimate. Preferred equity and noncontrolling interests are excluded; lease scope follows reported debt. Financial companies require separate revenue and cash-flow definitions; P/S, P/FCF and EV multiples are unavailable.",
    "Utility P/FCF is unavailable until group-wide capital-investment coverage is verified; provider capex can omit generating-project investments.",
]


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _split_table(actions: Sequence[CorporateAction]) -> tuple[list[date], list[float]]:
    # Multiple sync paths can retain the same action under different source IDs.
    factors: dict[date, float] = {}
    for action in actions:
        factor = number(action.split_factor)
        if factor is None or factor <= 0:
            raise ValueError("Invalid split factor; split basis cannot be verified.")
        previous = factors.get(action.ex_date)
        if previous is not None and not math.isclose(previous, factor):
            raise ValueError("Conflicting split factors; split basis cannot be verified.")
        factors[action.ex_date] = factor
    dates, cumulative = [], [1.0]
    for day, factor in sorted(factors.items()):
        dates.append(day)
        cumulative.append(cumulative[-1] * factor)
        if not math.isfinite(cumulative[-1]) or cumulative[-1] <= 0:
            raise ValueError("Invalid cumulative split factor.")
    return dates, cumulative


def _split_factor(start: date, end: date, table: tuple[list[date], list[float]]) -> float:
    dates, cumulative = table
    return cumulative[bisect_right(dates, end)] / cumulative[bisect_right(dates, start)]


def _statement(row: Any, reference_date: date, splits: tuple, fallback_vintage: date | None, fundamentals: dict, *, annual: bool = False) -> dict | None:
    income, balance, cash = row.income_statement or {}, row.balance_sheet or {}, row.cash_flow or {}
    period_end = _date(getattr(row, "period_end", None) or getattr(row, "fiscal_date", None))
    if period_end is None or getattr(row, "availability_estimated", False):
        return None
    filing_dates = [_date(part.get("filing_date") or part.get("filingDate") or part.get("dateFiled")) for part in (income, balance, cash)]
    filed = _date(getattr(row, "filing_at", None))
    available = _date(getattr(row, "available_at", None))
    # A later fetch/revision date cannot turn a fiscal-end placeholder into
    # evidence that the financials were disclosed on that date.
    known_filings = [d for d in [*filing_dates, filed] if d is not None]
    if not known_filings or any(d <= period_end for d in known_filings):
        return None
    known_dates = [*known_filings, *([available] if available else [])]
    vintage = _date(getattr(row, "fetched_at", None)) or fallback_vintage
    shares, _ = field(balance, "commonStockSharesOutstanding", "sharesOutstanding")
    source = getattr(row, "source", "EODHD")
    basis = balance.get("sharesBasis")
    if basis == "period_end":
        share_reference = period_end
    elif source == "EODHD":
        share_reference = vintage
    else:
        share_reference = None
    shares = shares * _split_factor(share_reference, reference_date, splits) if shares is not None and share_reference else None
    if shares is not None and (not math.isfinite(shares) or shares <= 0):
        shares = None
    # Currency is checked per metric, so missing cash-flow data cannot disable P/B.
    currencies = [str(part.get("currency_symbol") or "").upper() for part in (income, balance, cash)]
    # Older ingestions did not inherit a section's declared currency. Only
    # recover it from a matching source statement, never from today's quote.
    for index, (name, part) in enumerate(zip(("Income_Statement", "Balance_Sheet", "Cash_Flow"), (income, balance, cash))):
        section = fundamentals.get("Financials", {}).get(name, {})
        original = section.get("yearly" if annual else "quarterly", {}).get(period_end.isoformat())
        if not currencies[index] and original and part == original:
            currencies[index] = str(section.get("currency_symbol") or "").upper()
    net_income, _ = field(income, "netIncomeApplicableToCommonShares", "netIncome")
    cfo, _ = field(cash, "totalCashFromOperatingActivities", "operatingCashFlow")
    capex, _ = field(cash, "capitalExpenditures", "capitalExpenditure")
    cash_value, _ = field(balance, "cashAndShortTermInvestments", "cashAndCashEquivalents", "cashAndEquivalents", "cash")
    quality = statement_quality(*(dict(part, currency_symbol=currencies[index]) for index, part in enumerate((income, balance, cash))))
    return {
        "annual": annual, "quality": quality,
        "period_end": period_end, "available_after": max(known_dates),
        "revision": getattr(row, "revision", 1), "source": source,
        "raw_snapshot_id": getattr(row, "raw_snapshot_id", None),
        "share_reference_date": share_reference, "shares": shares,
        "currencies": currencies,
        "eps": net_income / shares if net_income is not None and shares else None,
        "net_income": number(income.get("netIncome")),
        "common_income": number(income.get("netIncomeApplicableToCommonShares")),
        "cfo": cfo, "capex": abs(capex) if capex is not None else None,
        "revenue": number(income.get("totalRevenue")), "ebitda": number(income.get("ebitda")),
        "fcf": cfo - abs(capex) if cfo is not None and capex is not None else None,
        "book": number(balance.get("totalStockholderEquity")),
        "debt": resolve_debt(balance)["value"],
        "cash": cash_value if cash_value is not None and cash_value >= 0 else None,
    }


def _consecutive(recent: Sequence[dict]) -> bool:
    return len(recent) == 4 and all(
        60 <= (left["period_end"] - right["period_end"]).days <= 130
        for left, right in zip(recent, recent[1:])
    ) and 240 <= (recent[0]["period_end"] - recent[-1]["period_end"]).days <= 310


def _annual_conflicts(rows: dict[date, dict], annuals: dict[date, dict], recent: Sequence[dict]) -> tuple[dict, list[dict]]:
    """Validate overlapping quarters against annual facts available this session.

    We cannot identify which quarter is wrong from an annual discrepancy, so
    every overlapping TTM window for that input is unavailable until it agrees.
    """
    reasons, checked = {}, []
    parts = {"net_income": 0, "common_income": 0, "revenue": 0, "ebitda": 0, "cfo": 2, "capex": 2}
    affected = {"net_income": ("pe",), "common_income": ("pe",), "revenue": ("ps", "ev_revenue"),
                "ebitda": ("ev_ebitda",), "cfo": ("pfcf",), "capex": ("pfcf",)}
    for end, annual in annuals.items():
        quarters = sorted((r for day, r in rows.items() if day <= end), key=lambda r: r["period_end"], reverse=True)[:4]
        if not _consecutive(quarters) or quarters[0]["period_end"] != end:
            continue
        if not {r["period_end"] for r in recent}.intersection(r["period_end"] for r in quarters):
            continue
        checked.append(annual)
        for key, part in parts.items():
            values, total = [r[key] for r in quarters], annual[key]
            if total is None or any(v is None for v in values):
                continue
            if not annual["currencies"][part] or any(r["currencies"][part] != annual["currencies"][part] for r in quarters):
                continue
            # Allow source rounding, including loss/profit cancellations. This
            # tests monetary totals, never the non-additive annual diluted EPS.
            tolerance = max(1.0, .02 * max(abs(total), sum(abs(v) for v in values)))
            if abs(sum(values) - total) > tolerance:
                for metric in affected[key]:
                    reasons[metric] = f"Quarterly {key.replace('_', ' ')} does not reconcile with the disclosed {end.isoformat()} annual statement."
    return reasons, checked


def _basis(rows: dict[date, dict], basis_id: int, available_from: date, currency: str | None, sector: str | None, annuals: dict[date, dict]) -> dict:
    recent = sorted(rows.values(), key=lambda row: row["period_end"], reverse=True)[:4]
    latest = recent[0]
    complete = _consecutive(recent)
    values = {key: None for key in ("eps", "revenue", "fcf", "ebitda")}
    reasons: dict[str, str] = {}
    required_parts = {"pe": [0, 1], "ps": [0, 1], "pb": [1], "pfcf": [1, 2], "ev_revenue": [0, 1], "ev_ebitda": [0, 1]}
    for key in KEYS:
        required_rows = [latest] if key == "pb" else recent
        if not currency or any(row["currencies"][part] != currency.upper() for row in required_rows for part in required_parts[key]):
            reasons[key] = "Matching price and statement currencies are required; FX and depositary-receipt conversions are not inferred."
        elif key != "pb" and not complete:
            reasons[key] = "Four consecutive disclosed quarters are required."
        elif latest["shares"] is None:
            reasons[key] = "A positive provider share count with a known split reference is required."
    mapping = {"pe": "eps", "ps": "revenue", "pfcf": "fcf", "ev_revenue": "revenue", "ev_ebitda": "ebitda"}
    if complete:
        for key in values:
            components = [row[key] for row in recent]
            values[key] = number(sum(components)) if all(v is not None for v in components) else None
    for key, field_name in {**mapping, "pb": "book"}.items():
        denominator = latest["book"] if key == "pb" else values[field_name]
        if key not in reasons and denominator is None:
            reasons[key] = {
                "eps": "Reported net income and a valid provider share count are required in every quarter.",
                "fcf": "Operating cash flow and capex are required in every quarter; missing values are not zero.",
            }.get(field_name, f"Complete {field_name.upper()} inputs are unavailable.")
        elif key not in reasons and denominator <= 0:
            reasons[key] = f"{field_name.upper()} is zero or negative; this multiple is not meaningful."
        for row in ([latest] if key == "pb" else recent):
            if field_name in row["quality"]:
                reasons[key] = f"{row['period_end'].isoformat()}: {row['quality'][field_name]}"
                break
    annual_reasons, checked_annuals = _annual_conflicts(rows, annuals, recent)
    reasons.update(annual_reasons)
    financial = (sector or "").lower() in {"financial services", "financials", "financial"}
    if financial:
        for key in ("ps", "pfcf"):
            reasons[key] = "Financial companies require separate revenue and cash-flow definitions; this multiple is unavailable."
        if any(row["common_income"] is None for row in recent):
            reasons["pe"] = "Disclosed earnings attributable to common shareholders are required for financial companies."
    if (sector or "").lower() == "utilities":
        reasons["pfcf"] = "Utility capital-investment coverage is not verified; provider capex can omit generating-project investments."
    for key in ("ev_revenue", "ev_ebitda"):
        if financial:
            reasons[key] = "Enterprise-value multiples are not comparable for financial companies."
        elif "debt" in latest["quality"]:
            reasons[key] = latest["quality"]["debt"]
        elif latest["debt"] is None or latest["cash"] is None:
            reasons[key] = "A complete, consistent debt total and cash balance are required for EV."
    return {
        "id": basis_id, "period_end": latest["period_end"].isoformat(),
        "available_from": available_from.isoformat(),
        "periods": [row["period_end"].isoformat() for row in recent],
        "source": ", ".join(sorted({row["source"] for row in recent})),
        "raw_snapshot_ids": sorted({row["raw_snapshot_id"] for row in [*recent, *checked_annuals] if row["raw_snapshot_id"] is not None}),
        "share_reference_dates": sorted({row["share_reference_date"].isoformat() for row in recent if row["share_reference_date"]}),
        "inputs": {**values, "shares": latest["shares"], "book": latest["book"], "debt": latest["debt"], "cash": latest["cash"]},
        "reasons": reasons,
    }


def _sample_date(day: date, interval: str) -> date:
    if interval == "1wk":
        return day + timedelta(days=(4 - day.weekday()) % 7)
    if interval == "1mo":
        return date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])
    return day


def build_valuation_history(
    ticker: str, prices: Sequence[Any], statements: Sequence[Any], actions: Sequence[Any],
    *, interval: str = "1d", currency: str | None = None, sector: str | None = None,
    fallback_vintage: date | None = None,
    fundamentals: dict | None = None,
    split_history_verified: bool = False,
    annual_statements: Sequence[Any] = (),
) -> dict:
    if interval not in {"1d", "1wk", "1mo"}:
        raise ValueError("Unsupported interval")
    prices = sorted(prices, key=lambda row: row.date)
    reference = prices[-1].date if prices else None
    warnings = []
    basis_error = None if split_history_verified else UNVERIFIED_REASON
    if basis_error:
        warnings.append(basis_error)
    fundamentals = fundamentals or {}
    general = fundamentals.get("General") or {}
    sector = sector or general.get("Sector")
    category = str(general.get("HomeCategory") or "").upper()
    if category.startswith("ADR") or "DEPOSITARY" in str(general.get("Type") or "").upper():
        basis_error = "Depositary-receipt share conversion is not available; company shares cannot be multiplied by receipt prices."
    try:
        splits = _split_table(actions)
    except ValueError as exc:
        splits, basis_error = ([], [1.0]), str(exc)
        split_history_verified = False
        warnings.append(basis_error)
    events = []
    if reference and not basis_error:
        for annual, source_rows in ((False, statements), (True, annual_statements)):
            for row in source_rows:
                event = _statement(row, reference, splits, fallback_vintage, fundamentals, annual=annual)
                if event:
                    events.append(event)
    events.sort(key=lambda row: (row["available_after"], row["revision"], row["period_end"]))
    if len(events) < len(statements) + len(annual_statements):
        warnings.append("Some statements were excluded because filing dates or split inputs could not be verified; fiscal-end filing placeholders are not disclosure evidence.")
    if not prices:
        warnings.append("No local daily prices are available.")
    if not any(not event["annual"] for event in events):
        warnings.append("No eligible quarterly statements are available.")
    states, annuals, bases, sampled = {}, {}, [], {}
    cursor, active = 0, None
    for price in prices:
        changed = False
        while cursor < len(events) and events[cursor]["available_after"] < price.date:
            event = events[cursor]
            target = annuals if event["annual"] else states
            previous = target.get(event["period_end"])
            if previous is None or event["revision"] >= previous["revision"]:
                target[event["period_end"]] = event
                changed = True
            cursor += 1
        if changed and states:
            active = _basis(states, len(bases), price.date, currency, sector, annuals)
            bases.append(active)
        reason = basis_error
        close = number(price.close)
        if reason is None and (close is None or close <= 0):
            reason = "Raw closing price is unavailable; dividend-adjusted prices are not substituted."
        if reason is None and active is None:
            reason = "No eligible quarterly statements had been disclosed."
        if reason is None and (price.date - date.fromisoformat(active["period_end"])).days > 180:
            reason = "The latest quarterly statement is more than 180 days old."
        values = dict.fromkeys(KEYS)
        ev_reason = None
        if reason is None:
            split_price = close / _split_factor(price.date, reference, splits)
            inputs = active["inputs"]
            equity = number(split_price * inputs["shares"]) if inputs["shares"] is not None else None
            ev = number(equity + inputs["debt"] - inputs["cash"]) if all(v is not None for v in (equity, inputs["debt"], inputs["cash"])) else None
            if ev is not None and ev <= 0:
                ev_reason = "Simplified enterprise value is zero or negative; EV multiples are not meaningful."
            denominators = {"pe": "eps", "ps": "revenue", "pb": "book", "pfcf": "fcf", "ev_revenue": "revenue", "ev_ebitda": "ebitda"}
            for key in KEYS:
                numerator = split_price if key == "pe" else ev if key.startswith("ev_") else equity
                if key not in active["reasons"] and numerator is not None and numerator > 0:
                    values[key] = number(numerator / inputs[denominators[key]])
        label = _sample_date(price.date, interval).isoformat()
        # Keep the final session's ratio, never average daily ratios and never
        # forward-fill the prior valid value over a missing final observation.
        sampled[label] = {"date": label, "price_date": price.date.isoformat(), "basis_id": active["id"] if active else None, "values": values, "reason": reason, "ev_reason": ev_reason}
    points = list(sampled.values())
    metrics = []
    for key, label, description, formula in METRICS:
        valid = [point["values"][key] for point in points if point["values"][key] is not None]
        last = points[-1] if points else None
        latest_value = last["values"][key] if last else None
        latest_reason = None
        if latest_value is None:
            latest_reason = last["reason"] if last else "No local price history."
            if last and key.startswith("ev_"):
                latest_reason = latest_reason or last["ev_reason"]
            if last and last["basis_id"] is not None:
                latest_reason = latest_reason or bases[last["basis_id"]]["reasons"].get(key)
            latest_reason = latest_reason or "This multiple is unavailable."
        metrics.append({"key": key, "label": label, "description": description, "formula": formula,
                        "valid_points": len(valid), "total_points": len(points),
                        "latest_value": latest_value, "latest_date": last["price_date"] if last else None,
                        "latest_reason": latest_reason, "median": median(valid) if valid else None})
    return {"ticker": ticker, "interval": interval, "currency": currency, "price_basis": "split_only",
            "history_basis": "reconstructed_estimates", "split_history_verified": split_history_verified, "split_reference_date": reference.isoformat() if reference else None,
            "methodology": METHODOLOGY, "warnings": warnings, "metrics": metrics, "bases": bases, "points": points}


async def get_valuation_history(ticker: str, db: AsyncSession, interval: str = "1d") -> dict:
    profile = await db.get(Ticker, ticker)
    snapshot, fundamentals = await load_latest_fundamentals_snapshot(ticker, db)
    prices = (await db.execute(select(DailyPrice).where(DailyPrice.ticker == ticker).order_by(DailyPrice.date))).scalars().all()
    versions = list((await db.execute(select(FundamentalVersion).where(
        FundamentalVersion.ticker == ticker, FundamentalVersion.period_type.in_(("Quarterly", "Yearly")),
    ))).scalars().all())
    # Legacy-only periods can still be reconstructed when their payload carries
    # a filing date. Never mix a mutable statement into an already versioned period.
    legacy = (await db.execute(select(FinancialStatement).where(
        FinancialStatement.ticker == ticker, FinancialStatement.period.in_(("Quarterly", "Yearly")),
    ))).scalars().all()
    covered = {(row.period_type, row.period_end) for row in versions}
    all_statements = [*versions, *(row for row in legacy if (row.period, row.fiscal_date) not in covered)]
    statements = [row for row in all_statements if (getattr(row, "period_type", None) or row.period) == "Quarterly"]
    annual_statements = [row for row in all_statements if (getattr(row, "period_type", None) or row.period) == "Yearly"]
    vintage = _date(snapshot.fetched_at) if snapshot else _date(profile.last_updated) if profile else None
    # Cover both quote dates and every statement share vintage, including
    # revisions fetched after the final price observation.
    required_dates = [row.date for row in prices]
    for row in statements:
        required_dates.extend(filter(None, (
            _date(getattr(row, "period_end", None) or getattr(row, "fiscal_date", None)),
            _date(getattr(row, "fetched_at", None)) or vintage,
        )))
    coverage = await load_split_history(db, ticker, min(required_dates), max(required_dates)) if required_dates else None
    result = build_valuation_history(ticker, prices, statements, coverage.actions if coverage else [], interval=interval,
                                     currency=profile.currency if profile else None,
                                     sector=profile.sector if profile else None,
                                     fallback_vintage=vintage, fundamentals=fundamentals,
                                     split_history_verified=coverage is not None,
                                     annual_statements=annual_statements)
    result["split_history_snapshot_id"] = coverage.snapshot_id if coverage else None
    result["split_history_through"] = coverage.through_date.isoformat() if coverage else None
    return result
