"""Read-only Finviz-style market snapshot for a single security."""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Iterable, Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    CorporateAction,
    DailyPrice,
    FinancialStatement,
    StockScreenerSnapshot,
    Ticker,
    UniverseMembership,
)
from services.decision_support import build_financial_context, build_peer_comparison
from services.events_expectations import (
    build_events_expectations_from_snapshot,
    load_latest_fundamentals_snapshot,
)
from services.screener_query import latest_published_screener_date
from services.security_master import canonicalize_ticker
from services.universe import (
    LIVE_UNIVERSE_SOURCE,
    SCREENER_INDEX_LABELS,
    SCREENER_MEMBERSHIP_UNIVERSES,
)


Metric = dict[str, Any]


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _first_value(mapping: Any, *keys: str) -> float | None:
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = _safe_float(mapping.get(key))
        if value is not None:
            return value
    return None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _effective_close(row: DailyPrice) -> float | None:
    adjusted = _safe_float(row.adjusted_close)
    if adjusted is not None and adjusted > 0:
        return adjusted
    close = _safe_float(row.close)
    return close if close is not None and close > 0 else None


def _adjusted_high_low(row: DailyPrice) -> tuple[float, float] | None:
    raw_close = _safe_float(row.close)
    adjusted_close = _safe_float(row.adjusted_close)
    raw_high = _safe_float(row.high)
    raw_low = _safe_float(row.low)
    if (
        raw_close is None
        or raw_close <= 0
        or adjusted_close is None
        or adjusted_close <= 0
        or raw_high is None
        or raw_low is None
    ):
        return None
    factor = adjusted_close / raw_close
    high = raw_high * factor
    low = raw_low * factor
    if not all(math.isfinite(value) and value > 0 for value in (high, low)):
        return None
    return high, low


def _ratio(numerator: Any, denominator: Any) -> float | None:
    num = _safe_float(numerator)
    den = _safe_float(denominator)
    if num is None or den is None or den <= 0:
        return None
    value = num / den
    return value if math.isfinite(value) else None


def _performance_for_years(
    prices: Sequence[tuple[date, float]],
    years: int,
) -> float | None:
    if not prices:
        return None
    latest_date, latest_price = prices[-1]
    target = latest_date - timedelta(days=365 * years)
    comparison = next(
        (price for observed, price in reversed(prices[:-1]) if observed <= target),
        None,
    )
    if comparison is None or comparison <= 0:
        return None
    result = latest_price / comparison - 1.0
    return result if math.isfinite(result) else None


def _expectation_value(
    expectations: Sequence[dict[str, Any]],
    periods: Iterable[str],
) -> float | None:
    wanted = set(periods)
    for item in expectations:
        if item.get("period") in wanted:
            return _safe_float(item.get("eps_average"))
    return None


def _metric(
    value: Any,
    unit: str,
    source_date: date | None,
    unavailable_reason: str,
    *,
    secondary_value: float | None = None,
    secondary_unit: str | None = None,
) -> Metric:
    available = value is not None and value != [] and value != ""
    return {
        "value": value if available else None,
        "unit": unit,
        "source_date": source_date,
        "unavailable_reason": None if available else unavailable_reason,
        "secondary_value": secondary_value if available else None,
        "secondary_unit": secondary_unit if available and secondary_value is not None else None,
        "percentile": None,
        "percentile_scope": None,
    }


def _snapshot_value(snapshot: StockScreenerSnapshot | None, key: str) -> float | None:
    return _safe_float(getattr(snapshot, key, None)) if snapshot is not None else None


async def get_market_snapshot(ticker: str, db: AsyncSession) -> dict[str, Any]:
    ticker = canonicalize_ticker(ticker)

    profile_result = await db.execute(select(Ticker).where(Ticker.ticker == ticker))
    profile = profile_result.scalar_one_or_none()

    screener_date = await latest_published_screener_date(db)
    snapshot: StockScreenerSnapshot | None = None
    if screener_date is not None:
        snapshot_result = await db.execute(
            select(StockScreenerSnapshot).where(
                StockScreenerSnapshot.ticker == ticker,
                StockScreenerSnapshot.date == screener_date,
            )
        )
        snapshot = snapshot_result.scalar_one_or_none()

    prices_result = await db.execute(
        select(DailyPrice)
        .where(DailyPrice.ticker == ticker)
        .order_by(DailyPrice.date.asc())
    )
    price_rows = list(prices_result.scalars().all())
    prices = [
        (row.date, close)
        for row in price_rows
        if (close := _effective_close(row)) is not None
    ]
    price_date = prices[-1][0] if prices else None
    current_price = prices[-1][1] if prices else _snapshot_value(snapshot, "close")
    previous_close = prices[-2][1] if len(prices) >= 2 else None

    statement_result = await db.execute(
        select(FinancialStatement)
        .where(
            FinancialStatement.ticker == ticker,
            FinancialStatement.period == "Quarterly",
        )
        .order_by(FinancialStatement.fiscal_date.desc())
        .limit(8)
    )
    statements = list(statement_result.scalars().all())

    actions_result = await db.execute(
        select(CorporateAction)
        .where(CorporateAction.ticker == ticker)
        .order_by(CorporateAction.ex_date.asc())
    )
    actions = list(actions_result.scalars().all())
    split_actions = [action for action in actions if action.action_type == "split"]
    financial = build_financial_context(
        statements,
        split_actions=split_actions,
        share_reference_date=price_date or screener_date,
    )
    financial_date = financial.get("latest_statement_date")
    ttm = financial["current_ttm"]
    balance = financial["latest_balance"]

    raw_snapshot, payload = await load_latest_fundamentals_snapshot(ticker, db)
    payload = payload or {}
    highlights = payload.get("Highlights") or {}
    splits_dividends = payload.get("SplitsDividends") or {}
    provider_date = raw_snapshot.fetched_at.date() if raw_snapshot and raw_snapshot.fetched_at else None
    expectations_data = build_events_expectations_from_snapshot(
        ticker,
        raw_snapshot,
        payload or None,
    )
    expectations = expectations_data.get("expectations") or []

    membership_labels: list[str] = []
    if screener_date is not None:
        membership_result = await db.execute(
            select(UniverseMembership.universe)
            .where(
                UniverseMembership.ticker == ticker,
                UniverseMembership.universe.in_(SCREENER_MEMBERSHIP_UNIVERSES),
                UniverseMembership.source == LIVE_UNIVERSE_SOURCE,
                UniverseMembership.effective_from <= screener_date,
                or_(
                    UniverseMembership.effective_to.is_(None),
                    UniverseMembership.effective_to >= screener_date,
                ),
            )
            .distinct()
        )
        memberships = set(membership_result.scalars().all())
        membership_labels = [
            label
            for universe, label in SCREENER_INDEX_LABELS.items()
            if universe in memberships
        ]

    peer_percentiles: dict[str, tuple[float, str]] = {}
    if snapshot is not None:
        cohort_filters = []
        if snapshot.industry:
            cohort_filters.append(StockScreenerSnapshot.industry == snapshot.industry)
        if snapshot.sector:
            cohort_filters.append(StockScreenerSnapshot.sector == snapshot.sector)
        peers: list[StockScreenerSnapshot] = [snapshot]
        if cohort_filters:
            peer_result = await db.execute(
                select(StockScreenerSnapshot).where(
                    StockScreenerSnapshot.date == screener_date,
                    or_(*cohort_filters),
                )
            )
            peers = list(peer_result.scalars().all())
        comparison = build_peer_comparison(snapshot, peers)
        peer_percentiles = {
            item["key"]: (float(item["summary_percentile"]), item["summary_scope"])
            for item in comparison["metrics"]
            if item.get("summary_percentile") is not None and item.get("summary_scope")
        }

    cash = _safe_float(balance.get("cash_and_short_term_investments"))
    if cash is None:
        cash = _safe_float(balance.get("cash"))
    debt = _safe_float(balance.get("debt"))
    equity = _safe_float(balance.get("equity"))
    shares = _safe_float(balance.get("shares"))
    market_cap = _snapshot_value(snapshot, "market_cap")
    enterprise_value = (
        market_cap + debt - cash
        if market_cap is not None and debt is not None and cash is not None
        else None
    )

    dividend_actions = [
        action
        for action in actions
        if action.action_type == "dividend" and action.cash_amount is not None
    ]
    dividend_ttm: float | None = None
    if price_date is not None:
        recent_dividends = [
            _safe_float(action.cash_amount)
            for action in dividend_actions
            if price_date - timedelta(days=365) < action.ex_date <= price_date
        ]
        if recent_dividends:
            dividend_ttm = sum(value for value in recent_dividends if value is not None)
        elif _snapshot_value(snapshot, "dividend_yield") == 0:
            dividend_ttm = 0.0

    high_low_window = [
        values
        for row in price_rows[-252:]
        if (values := _adjusted_high_low(row)) is not None
    ] if len(price_rows) >= 252 else []
    high_52w = max((high for high, _ in high_low_window), default=None) if len(high_low_window) == 252 else None
    low_52w = min((low for _, low in high_low_window), default=None) if len(high_low_window) == 252 else None
    high_52w_ratio = _ratio(current_price, high_52w)
    low_52w_ratio = _ratio(current_price, low_52w)
    high_52w_distance = high_52w_ratio - 1.0 if high_52w_ratio is not None else None
    low_52w_distance = low_52w_ratio - 1.0 if low_52w_ratio is not None else None

    screener_missing = (
        "The latest published Screener snapshot does not contain this value."
        if snapshot is not None
        else "Ticker is not present in the latest published Screener snapshot."
    )
    financial_missing = "A complete latest financial statement value is unavailable."
    ttm_missing = "Four contiguous quarterly statements are required for this TTM value."
    provider_missing = "The local provider snapshot does not publish this value."
    price_missing = "Adjusted price history is unavailable."
    history_missing = "The required adjusted price history is not long enough."

    metrics: dict[str, Metric] = {
        "index_membership": _metric(membership_labels, "text", screener_date, screener_missing),
        "market_cap": _metric(market_cap, "currency", screener_date, screener_missing),
        "enterprise_value": _metric(enterprise_value, "currency", financial_date, financial_missing),
        "sales_ttm": _metric(ttm.get("revenue"), "currency", financial_date, ttm_missing),
        "net_income_ttm": _metric(ttm.get("net_income"), "currency", financial_date, ttm_missing),
        "book_per_share": _metric(_ratio(equity, shares), "currency", financial_date, financial_missing),
        "cash_per_share": _metric(_ratio(cash, shares), "currency", financial_date, financial_missing),
        "shares_outstanding": _metric(_snapshot_value(snapshot, "shares_outstanding"), "integer", screener_date, screener_missing),
        "shares_float": _metric(_snapshot_value(snapshot, "shares_float"), "integer", screener_date, screener_missing),
        "short_float": _metric(_snapshot_value(snapshot, "short_float"), "percent", screener_date, screener_missing),
        "ipo_date": _metric(snapshot.ipo_date if snapshot else None, "date", screener_date, screener_missing),
        "dividend_estimate": _metric(
            expectations_data.get("annual_dividend_per_share")
            if expectations_data.get("annual_dividend_per_share") is not None
            else (0.0 if _snapshot_value(snapshot, "dividend_yield") == 0 else None),
            "currency",
            provider_date,
            provider_missing,
        ),
        "dividend_ttm": _metric(dividend_ttm, "currency", price_date, provider_missing),
        "dividend_ex_date": _metric(_parse_date(splits_dividends.get("ExDividendDate")), "date", provider_date, provider_missing),
        "dividend_growth_3yr": _metric(_snapshot_value(snapshot, "dividend_growth_3yr"), "percent", screener_date, screener_missing),
        "dividend_growth_5yr": _metric(_snapshot_value(snapshot, "dividend_growth_5yr"), "percent", screener_date, screener_missing),
        "payout_ratio": _metric(_snapshot_value(snapshot, "payout_ratio"), "percent", screener_date, screener_missing),
        "current_ratio": _metric(_snapshot_value(snapshot, "current_ratio"), "ratio", screener_date, screener_missing),
        "quick_ratio": _metric(_snapshot_value(snapshot, "quick_ratio"), "ratio", screener_date, screener_missing),
        "debt_to_equity": _metric(_snapshot_value(snapshot, "debt_to_equity"), "ratio", screener_date, screener_missing),
        "lt_debt_to_equity": _metric(_snapshot_value(snapshot, "lt_debt_to_equity"), "ratio", screener_date, screener_missing),
        "pe_ratio": _metric(_snapshot_value(snapshot, "pe_ratio"), "multiple", screener_date, "P/E is unavailable when trailing earnings are non-positive or missing."),
        "forward_pe": _metric(_snapshot_value(snapshot, "forward_pe"), "multiple", screener_date, screener_missing),
        "peg_ratio": _metric(_snapshot_value(snapshot, "peg_ratio"), "multiple", screener_date, "PEG requires positive earnings and expected growth."),
        "ps_ratio": _metric(_snapshot_value(snapshot, "ps_ratio"), "multiple", screener_date, screener_missing),
        "pb_ratio": _metric(_snapshot_value(snapshot, "pb_ratio"), "multiple", screener_date, "P/B requires positive book equity."),
        "price_cash": _metric(_snapshot_value(snapshot, "price_cash"), "multiple", screener_date, screener_missing),
        "price_fcf": _metric(_snapshot_value(snapshot, "price_fcf"), "multiple", screener_date, "Price/FCF requires positive free cash flow."),
        "ev_sales": _metric(_snapshot_value(snapshot, "ev_sales"), "multiple", screener_date, screener_missing),
        "ev_ebitda": _metric(_snapshot_value(snapshot, "ev_ebitda"), "multiple", screener_date, "EV/EBITDA requires positive EBITDA."),
        "eps_ttm": _metric(_first_value(highlights, "EarningsShare", "DilutedEpsTTM"), "currency", provider_date, provider_missing),
        "eps_next_quarter": _metric(_expectation_value(expectations, ("0q", "+1q")), "currency", provider_date, provider_missing),
        "eps_next_year": _metric(_expectation_value(expectations, ("+1y",)), "currency", provider_date, provider_missing),
        "eps_growth_this_year": _metric(_snapshot_value(snapshot, "eps_growth_this_year"), "percent", screener_date, screener_missing),
        "eps_growth_next_year": _metric(_snapshot_value(snapshot, "eps_growth_next_year"), "percent", screener_date, screener_missing),
        "eps_growth_qoq": _metric(_snapshot_value(snapshot, "eps_growth_qoq"), "percent", screener_date, screener_missing),
        "eps_growth_ttm": _metric(_snapshot_value(snapshot, "eps_growth_ttm"), "percent", screener_date, screener_missing),
        "eps_growth_3yr": _metric(_snapshot_value(snapshot, "eps_growth_3yr"), "percent", screener_date, screener_missing),
        "eps_growth_5yr": _metric(_snapshot_value(snapshot, "eps_growth_5yr"), "percent", screener_date, screener_missing),
        "sales_growth_qoq": _metric(_snapshot_value(snapshot, "sales_growth_qoq"), "percent", screener_date, screener_missing),
        "sales_growth_ttm": _metric(_snapshot_value(snapshot, "sales_growth_ttm"), "percent", screener_date, screener_missing),
        "sales_growth_3yr": _metric(_snapshot_value(snapshot, "sales_growth_3yr"), "percent", screener_date, screener_missing),
        "sales_growth_5yr": _metric(_snapshot_value(snapshot, "sales_growth_5yr"), "percent", screener_date, screener_missing),
        "roa": _metric(_snapshot_value(snapshot, "roa"), "percent", screener_date, screener_missing),
        "roe": _metric(_snapshot_value(snapshot, "roe"), "percent", screener_date, screener_missing),
        "roic": _metric(_snapshot_value(snapshot, "roic"), "percent", screener_date, screener_missing),
        "gross_margin": _metric(_snapshot_value(snapshot, "gross_margin"), "percent", screener_date, screener_missing),
        "operating_margin": _metric(_snapshot_value(snapshot, "operating_margin"), "percent", screener_date, screener_missing),
        "net_profit_margin": _metric(_snapshot_value(snapshot, "net_profit_margin"), "percent", screener_date, screener_missing),
        "insider_ownership": _metric(_snapshot_value(snapshot, "insider_ownership"), "percent", screener_date, screener_missing),
        "institutional_ownership": _metric(_snapshot_value(snapshot, "institutional_ownership"), "percent", screener_date, screener_missing),
    }

    for period in ("1w", "1m", "3m", "6m", "ytd", "1yr"):
        key = f"performance_{period}"
        metrics[key] = _metric(_snapshot_value(snapshot, key), "percent", screener_date, history_missing)
    for years in (3, 5, 10):
        metrics[f"performance_{years}yr"] = _metric(
            _performance_for_years(prices, years),
            "percent",
            price_date,
            history_missing,
        )

    technical_price = _snapshot_value(snapshot, "close")
    if technical_price is None:
        technical_price = current_price
    for period in (20, 50, 200):
        ma_value = _snapshot_value(snapshot, f"ma{period}")
        distance = _ratio(technical_price, ma_value)
        metrics[f"sma{period}_distance"] = _metric(
            distance - 1.0 if distance is not None else None,
            "percent",
            screener_date,
            history_missing,
            secondary_value=ma_value,
            secondary_unit="currency",
        )

    metrics.update({
        "high_52w": _metric(high_52w, "currency", price_date, history_missing, secondary_value=high_52w_distance, secondary_unit="percent"),
        "low_52w": _metric(low_52w, "currency", price_date, history_missing, secondary_value=low_52w_distance, secondary_unit="percent"),
        "volatility_1w": _metric(_snapshot_value(snapshot, "volatility_1w"), "percent", screener_date, history_missing),
        "volatility_1m": _metric(_snapshot_value(snapshot, "volatility_1m"), "percent", screener_date, history_missing),
        "atr_14": _metric(_snapshot_value(snapshot, "atr_14"), "currency", screener_date, history_missing),
        "rsi_14": _metric(_snapshot_value(snapshot, "rsi_14"), "number", screener_date, history_missing),
        "beta_1yr": _metric(_snapshot_value(snapshot, "beta_1yr"), "number", screener_date, history_missing),
        "relative_volume": _metric(_snapshot_value(snapshot, "relative_volume"), "ratio", screener_date, history_missing),
        "average_volume_3m": _metric(_snapshot_value(snapshot, "average_volume_3m"), "integer", screener_date, history_missing),
        "volume": _metric(_snapshot_value(snapshot, "volume"), "integer", screener_date, history_missing),
        "prev_close": _metric(previous_close, "currency", price_date, price_missing),
        "price": _metric(current_price, "currency", price_date, price_missing),
        "change": _metric(_snapshot_value(snapshot, "performance_1d"), "percent", screener_date, history_missing),
        "analyst_recommendation": _metric(_snapshot_value(snapshot, "analyst_recommendation"), "number", screener_date, screener_missing),
        "target_price": _metric(_snapshot_value(snapshot, "target_price"), "currency", screener_date, screener_missing),
    })

    for key, (percentile, scope) in peer_percentiles.items():
        if key in metrics:
            metrics[key]["percentile"] = percentile
            metrics[key]["percentile_scope"] = scope

    available = sum(
        1
        for item in metrics.values()
        if item["value"] is not None and item["value"] != [] and item["value"] != ""
    )
    total = len(metrics)
    return {
        "ticker": ticker,
        "currency": profile.currency if profile else None,
        "source_dates": {
            "price": price_date,
            "screener": screener_date,
            "financials": financial_date,
            "provider": provider_date,
        },
        "coverage": {
            "available": available,
            "total": total,
            "ratio": available / total if total else 0.0,
        },
        "metrics": metrics,
    }
