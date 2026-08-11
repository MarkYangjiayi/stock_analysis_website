"""Read-only Finviz-style market snapshot for a single security."""

from __future__ import annotations

import math
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any, Iterable, Sequence

import pandas as pd
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
from services.screener_metrics import (
    calculate_dividend_growth,
    calculate_price_metrics,
    extract_fundamental_metrics,
    validated_adjusted_returns,
)
from services.screener_normalization import normalize_public_screener_values
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


def _snapshot_value(snapshot: SimpleNamespace | None, key: str) -> float | None:
    return _safe_float(getattr(snapshot, key, None)) if snapshot is not None else None


def _normalized_screener_snapshot(
    snapshot: StockScreenerSnapshot | None,
) -> SimpleNamespace | None:
    """Return a detached snapshot with the canonical public value rules applied."""
    if snapshot is None:
        return None
    values = {
        column.name: getattr(snapshot, column.name)
        for column in StockScreenerSnapshot.__table__.columns
    }
    normalize_public_screener_values(values)
    return SimpleNamespace(**values)


def _price_frame(rows: Sequence[DailyPrice]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "date": row.date,
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "adjusted_close": row.adjusted_close,
            "volume": row.volume,
        }
        for row in rows
    ])


def _local_price_metrics(
    price_rows: Sequence[DailyPrice],
    benchmark_rows: Sequence[DailyPrice],
) -> dict[str, Any]:
    if not price_rows:
        return {}
    benchmark_returns = None
    if benchmark_rows and benchmark_rows[-1].date == price_rows[-1].date:
        benchmark = pd.Series(
            [_effective_close(row) for row in benchmark_rows],
            index=pd.to_datetime([row.date for row in benchmark_rows]),
            dtype="float64",
        )
        benchmark_returns = validated_adjusted_returns(benchmark)
    return calculate_price_metrics(
        _price_frame(price_rows[-400:]),
        benchmark_returns,
    )


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
    public_snapshot = _normalized_screener_snapshot(snapshot)

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
    current_price = prices[-1][1] if prices else _snapshot_value(public_snapshot, "close")
    previous_close = prices[-2][1] if len(prices) >= 2 else None
    change_ratio = _ratio(current_price, previous_close)
    latest_change = change_ratio - 1.0 if change_ratio is not None else None

    benchmark_rows: list[DailyPrice] = []
    if price_date is not None:
        if ticker == "SPY.US":
            benchmark_rows = price_rows[-400:]
        else:
            benchmark_result = await db.execute(
                select(DailyPrice)
                .where(
                    DailyPrice.ticker == "SPY.US",
                    DailyPrice.date <= price_date,
                    DailyPrice.date >= price_date - timedelta(days=400),
                )
                .order_by(DailyPrice.date.asc())
            )
            benchmark_rows = list(benchmark_result.scalars().all())
    local_price_metrics = _local_price_metrics(price_rows, benchmark_rows)

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

    provider_fundamentals = extract_fundamental_metrics(payload) if payload else {}
    fallback_values: dict[str, Any] = dict(provider_fundamentals)
    fallback_source_dates: dict[str, date | None] = {
        key: provider_date for key in provider_fundamentals
    }
    for key, value in local_price_metrics.items():
        fallback_values[key] = value
        fallback_source_dates[key] = price_date
    if price_rows:
        fallback_values.update({
            "close": current_price,
            "volume": _safe_float(price_rows[-1].volume),
        })
        fallback_source_dates.update({"close": price_date, "volume": price_date})

    dividend_actions = [
        action
        for action in actions
        if action.action_type == "dividend" and action.cash_amount is not None
    ]
    if price_date is not None:
        dividend_growth = calculate_dividend_growth(
            [(action.ex_date, action.cash_amount) for action in dividend_actions],
            price_date,
        )
        for key, value in dividend_growth.items():
            fallback_values[key] = value
            fallback_source_dates[key] = price_date

    effective_values: dict[str, Any] = {}
    metric_source_dates: dict[str, date | None] = {}
    snapshot_values = {
        column.name: getattr(public_snapshot, column.name, None)
        for column in StockScreenerSnapshot.__table__.columns
    }
    for key in set(snapshot_values) | set(fallback_values):
        snapshot_value = snapshot_values.get(key)
        if snapshot_value is not None and snapshot_value != "":
            effective_values[key] = snapshot_value
            metric_source_dates[key] = screener_date
        else:
            effective_values[key] = fallback_values.get(key)
            metric_source_dates[key] = fallback_source_dates.get(key)
    effective_values["sector"] = effective_values.get("sector") or (
        profile.sector if profile else None
    )
    effective_values["industry"] = effective_values.get("industry") or (
        profile.industry if profile else None
    )
    effective_snapshot = SimpleNamespace(**effective_values)

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
    if screener_date is not None:
        cohort_filters = []
        if effective_values.get("industry"):
            cohort_filters.append(
                StockScreenerSnapshot.industry == effective_values["industry"]
            )
        if effective_values.get("sector"):
            cohort_filters.append(
                StockScreenerSnapshot.sector == effective_values["sector"]
            )
        peers: list[StockScreenerSnapshot] = []
        if cohort_filters:
            peer_result = await db.execute(
                select(StockScreenerSnapshot).where(
                    StockScreenerSnapshot.date == screener_date,
                    or_(*cohort_filters),
                )
            )
            peers = list(peer_result.scalars().all())
        comparison = build_peer_comparison(
            effective_snapshot,
            [
                normalized
                for peer in peers
                if (normalized := _normalized_screener_snapshot(peer)) is not None
            ],
        )
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
    market_cap = _snapshot_value(effective_snapshot, "market_cap")
    enterprise_value = (
        market_cap + debt - cash
        if market_cap is not None and debt is not None and cash is not None
        else None
    )

    dividend_ttm: float | None = None
    if price_date is not None:
        recent_dividends = [
            _safe_float(action.cash_amount)
            for action in dividend_actions
            if price_date - timedelta(days=365) < action.ex_date <= price_date
        ]
        if recent_dividends:
            dividend_ttm = sum(value for value in recent_dividends if value is not None)
        elif _snapshot_value(effective_snapshot, "dividend_yield") == 0:
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
    fundamental_missing = (
        "Neither the latest Screener nor the local provider fundamentals "
        "contain this value."
    )
    financial_missing = "A complete latest financial statement value is unavailable."
    ttm_missing = "Four contiguous quarterly statements are required for this TTM value."
    provider_missing = "The local provider snapshot does not publish this value."
    price_missing = "Adjusted price history is unavailable."
    history_missing = "The required adjusted price history is not long enough."

    def resolved_value(key: str) -> Any:
        return getattr(effective_snapshot, key, None)

    def resolved_metric(
        key: str,
        unit: str,
        unavailable_reason: str = fundamental_missing,
    ) -> Metric:
        return _metric(
            resolved_value(key),
            unit,
            metric_source_dates.get(key),
            unavailable_reason,
        )

    enterprise_dates = [
        value
        for value in (metric_source_dates.get("market_cap"), financial_date)
        if value is not None
    ]
    enterprise_date = max(enterprise_dates) if enterprise_dates else None

    metrics: dict[str, Metric] = {
        "index_membership": _metric(membership_labels, "text", screener_date, screener_missing),
        "market_cap": resolved_metric("market_cap", "currency"),
        "enterprise_value": _metric(enterprise_value, "currency", enterprise_date, financial_missing),
        "sales_ttm": _metric(ttm.get("revenue"), "currency", financial_date, ttm_missing),
        "net_income_ttm": _metric(ttm.get("net_income"), "currency", financial_date, ttm_missing),
        "book_per_share": _metric(_ratio(equity, shares), "currency", financial_date, financial_missing),
        "cash_per_share": _metric(_ratio(cash, shares), "currency", financial_date, financial_missing),
        "shares_outstanding": resolved_metric("shares_outstanding", "integer"),
        "shares_float": resolved_metric("shares_float", "integer"),
        "short_float": resolved_metric("short_float", "percent"),
        "ipo_date": resolved_metric("ipo_date", "date"),
        "dividend_estimate": _metric(
            expectations_data.get("annual_dividend_per_share")
            if expectations_data.get("annual_dividend_per_share") is not None
            else (0.0 if _snapshot_value(effective_snapshot, "dividend_yield") == 0 else None),
            "currency",
            provider_date,
            provider_missing,
        ),
        "dividend_ttm": _metric(dividend_ttm, "currency", price_date, history_missing),
        "dividend_ex_date": _metric(_parse_date(splits_dividends.get("ExDividendDate")), "date", provider_date, provider_missing),
        "dividend_growth_3yr": resolved_metric("dividend_growth_3yr", "percent"),
        "dividend_growth_5yr": resolved_metric("dividend_growth_5yr", "percent"),
        "payout_ratio": resolved_metric("payout_ratio", "percent"),
        "current_ratio": resolved_metric("current_ratio", "ratio"),
        "quick_ratio": resolved_metric("quick_ratio", "ratio"),
        "debt_to_equity": resolved_metric("debt_to_equity", "ratio"),
        "lt_debt_to_equity": resolved_metric("lt_debt_to_equity", "ratio"),
        "pe_ratio": resolved_metric("pe_ratio", "multiple", "P/E is unavailable when trailing earnings are non-positive or missing."),
        "forward_pe": resolved_metric("forward_pe", "multiple"),
        "peg_ratio": resolved_metric("peg_ratio", "multiple", "PEG requires positive earnings and expected growth."),
        "ps_ratio": resolved_metric("ps_ratio", "multiple"),
        "pb_ratio": resolved_metric("pb_ratio", "multiple", "P/B requires positive book equity."),
        "price_cash": resolved_metric("price_cash", "multiple"),
        "price_fcf": resolved_metric("price_fcf", "multiple", "Price/FCF requires positive free cash flow."),
        "ev_sales": resolved_metric("ev_sales", "multiple"),
        "ev_ebitda": resolved_metric("ev_ebitda", "multiple", "EV/EBITDA requires positive EBITDA."),
        "eps_ttm": _metric(_first_value(highlights, "EarningsShare", "DilutedEpsTTM"), "currency", provider_date, provider_missing),
        "eps_next_quarter": _metric(_expectation_value(expectations, ("0q", "+1q")), "currency", provider_date, provider_missing),
        "eps_next_year": _metric(_expectation_value(expectations, ("+1y",)), "currency", provider_date, provider_missing),
        "eps_growth_this_year": resolved_metric("eps_growth_this_year", "percent"),
        "eps_growth_next_year": resolved_metric("eps_growth_next_year", "percent"),
        "eps_growth_qoq": resolved_metric("eps_growth_qoq", "percent"),
        "eps_growth_ttm": resolved_metric("eps_growth_ttm", "percent"),
        "eps_growth_3yr": resolved_metric("eps_growth_3yr", "percent"),
        "eps_growth_5yr": resolved_metric("eps_growth_5yr", "percent"),
        "sales_growth_qoq": resolved_metric("sales_growth_qoq", "percent"),
        "sales_growth_ttm": resolved_metric("sales_growth_ttm", "percent"),
        "sales_growth_3yr": resolved_metric("sales_growth_3yr", "percent"),
        "sales_growth_5yr": resolved_metric("sales_growth_5yr", "percent"),
        "roa": resolved_metric("roa", "percent"),
        "roe": resolved_metric("roe", "percent"),
        "roic": resolved_metric("roic", "percent"),
        "gross_margin": resolved_metric("gross_margin", "percent"),
        "operating_margin": resolved_metric("operating_margin", "percent"),
        "net_profit_margin": resolved_metric("net_profit_margin", "percent"),
        "insider_ownership": resolved_metric("insider_ownership", "percent"),
        "institutional_ownership": resolved_metric("institutional_ownership", "percent"),
    }

    for period in ("1w", "1m", "3m", "6m", "ytd", "1yr"):
        key = f"performance_{period}"
        metrics[key] = resolved_metric(key, "percent", history_missing)
    for years in (3, 5, 10):
        metrics[f"performance_{years}yr"] = _metric(
            _performance_for_years(prices, years),
            "percent",
            price_date,
            history_missing,
        )

    technical_price = _snapshot_value(effective_snapshot, "close")
    if technical_price is None:
        technical_price = current_price
    for period in (20, 50, 200):
        ma_key = f"ma{period}"
        ma_value = _snapshot_value(effective_snapshot, ma_key)
        distance = _ratio(technical_price, ma_value)
        metrics[f"sma{period}_distance"] = _metric(
            distance - 1.0 if distance is not None else None,
            "percent",
            metric_source_dates.get(ma_key),
            history_missing,
            secondary_value=ma_value,
            secondary_unit="currency",
        )

    metrics.update({
        "high_52w": _metric(high_52w, "currency", price_date, history_missing, secondary_value=high_52w_distance, secondary_unit="percent"),
        "low_52w": _metric(low_52w, "currency", price_date, history_missing, secondary_value=low_52w_distance, secondary_unit="percent"),
        "volatility_1w": resolved_metric("volatility_1w", "percent", history_missing),
        "volatility_1m": resolved_metric("volatility_1m", "percent", history_missing),
        "atr_14": resolved_metric("atr_14", "currency", history_missing),
        "rsi_14": resolved_metric("rsi_14", "number", history_missing),
        "beta_1yr": resolved_metric("beta_1yr", "number", history_missing),
        "relative_volume": resolved_metric("relative_volume", "ratio", history_missing),
        "average_volume_3m": resolved_metric("average_volume_3m", "integer", history_missing),
        "volume": resolved_metric("volume", "integer", history_missing),
        "prev_close": _metric(previous_close, "currency", price_date, price_missing),
        "price": _metric(current_price, "currency", price_date, price_missing),
        "change": _metric(latest_change, "percent", price_date, price_missing),
        "analyst_recommendation": resolved_metric("analyst_recommendation", "number"),
        "target_price": resolved_metric("target_price", "currency"),
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
