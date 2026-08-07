"""Normalization and derived-metric helpers for the daily stock screener."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd
import pandas_ta_classic as ta

from core.trading_calendar import is_us_market_session
from services.screener_normalization import (
    normalize_multiple,
    normalize_public_screener_values,
)


# An 11x close-to-close move in an adjusted series is overwhelmingly a stale
# constituent or missed corporate action, not a comparable index-stock return.
# Quarantine the derived technical set instead of clipping the observation.
MAX_ADJUSTED_DAILY_RETURN = 10.0
PRICE_METRIC_FIELDS = (
    "ma20",
    "ma50",
    "ma200",
    "rsi_14",
    "average_volume_3m",
    "relative_volume",
    "performance_1d",
    "performance_1w",
    "performance_1m",
    "performance_3m",
    "performance_6m",
    "performance_ytd",
    "performance_1yr",
    "volatility_1w",
    "volatility_1m",
    "gap",
    "change_from_open",
    "high_20d_rel",
    "low_20d_rel",
    "high_50d_rel",
    "low_50d_rel",
    "high_52w_rel",
    "low_52w_rel",
    "beta_1yr",
    "atr_14",
    "candlestick",
)


def safe_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None


def safe_decimal_rate(value: Any) -> Optional[float]:
    """Normalize provider fields documented as decimal ratios."""
    return safe_float(value)


def safe_percentage_points(value: Any) -> Optional[float]:
    """Normalize provider fields documented in percentage points."""
    result = safe_float(value)
    return result / 100.0 if result is not None else None


def normalize_peg_ratio(value: Any) -> Optional[float]:
    """Return an economically meaningful PEG value for screening.

    A non-positive provider PEG generally reflects non-positive earnings or
    expected growth, so it must not rank as a deceptively cheap valuation.
    """
    return normalize_multiple("peg_ratio", value)


def validated_adjusted_returns(adjusted_close: pd.Series) -> Optional[pd.Series]:
    """Return adjusted-price returns unless the source series is unsafe for beta."""
    prices = pd.to_numeric(adjusted_close, errors="coerce")
    populated = prices.dropna()
    if prices.isna().any() or populated.empty or (populated <= 0).any():
        return None
    returns = prices.pct_change(fill_method=None)
    populated_returns = returns.dropna()
    if (
        (populated_returns > MAX_ADJUSTED_DAILY_RETURN).any()
        or (populated_returns < -1).any()
    ):
        return None
    return populated_returns


def safe_ratio(numerator: Any, denominator: Any, *, positive_denominator: bool = True) -> Optional[float]:
    num = safe_float(numerator)
    den = safe_float(denominator)
    if num is None or den is None or den == 0:
        return None
    if positive_denominator and den <= 0:
        return None
    result = num / den
    return result if np.isfinite(result) else None


def _first_present(*values: Any) -> Any:
    return next((value for value in values if value is not None and value != ""), None)


def _dated_values(section: Any) -> list[dict]:
    if isinstance(section, dict):
        values = [
            {**value, "date": value.get("date") or value.get("reportDate") or key}
            for key, value in section.items()
            if isinstance(value, dict)
        ]
    elif isinstance(section, list):
        values = [value for value in section if isinstance(value, dict)]
    else:
        return []
    return sorted(values, key=lambda row: str(row.get("date") or row.get("reportDate") or ""), reverse=True)


def _growth(new: Any, old: Any) -> Optional[float]:
    new_value = safe_float(new)
    old_value = safe_float(old)
    if new_value is None or old_value is None or old_value == 0:
        return None
    result = new_value / old_value - 1.0
    return result if np.isfinite(result) else None


def _growth_with_positive_base(new: Any, old: Any) -> Optional[float]:
    old_value = safe_float(old)
    if old_value is None or old_value <= 0:
        return None
    return _growth(new, old_value)


def _cagr(new: Any, old: Any, years: int) -> Optional[float]:
    new_value = safe_float(new)
    old_value = safe_float(old)
    if new_value is None or old_value is None or new_value <= 0 or old_value <= 0 or years <= 0:
        return None
    result = (new_value / old_value) ** (1.0 / years) - 1.0
    return result if np.isfinite(result) else None


def _annual_change(
    rows: list[dict],
    key: str,
    years: int,
    *,
    cagr: bool,
) -> Optional[float]:
    if not rows:
        return None
    try:
        latest_date = pd.Timestamp(rows[0].get("date") or rows[0].get("reportDate"))
    except (TypeError, ValueError):
        return None
    if pd.isna(latest_date):
        return None
    target_year = latest_date.year - years
    comparison = None
    for row in rows[1:]:
        try:
            report_date = pd.Timestamp(row.get("date") or row.get("reportDate"))
        except (TypeError, ValueError):
            continue
        if not pd.isna(report_date) and report_date.year == target_year:
            comparison = row
            break
    if comparison is None:
        return None
    if cagr:
        return _cagr(rows[0].get(key), comparison.get(key), years)
    return _growth_with_positive_base(rows[0].get(key), comparison.get(key))


def _sum_metric(rows: list[dict], key: str, start: int, count: int) -> Optional[float]:
    window = rows[start:start + count]
    values = [safe_float(row.get(key)) for row in window]
    if len(window) != count or any(value is None for value in values):
        return None
    latest_period = _fiscal_period(rows[0]) if rows else None
    if latest_period is None:
        return None
    latest_index = latest_period[0] * 4 + latest_period[1] - 1
    for offset, row in enumerate(window, start=start):
        period = _fiscal_period(row)
        if period is None or period[0] * 4 + period[1] - 1 != latest_index - offset:
            return None
    return float(sum(value for value in values if value is not None))


def _fiscal_period(row: dict) -> Optional[tuple[int, int]]:
    fiscal_year = safe_float(row.get("fiscalYear"))
    fiscal_quarter = str(
        row.get("fiscalQuarter") or row.get("quarter") or ""
    ).strip().upper()
    if fiscal_year is not None and fiscal_quarter:
        try:
            quarter = int(fiscal_quarter.removeprefix("Q"))
        except ValueError:
            quarter = 0
        if fiscal_year.is_integer() and 1 <= quarter <= 4:
            return int(fiscal_year), quarter
    try:
        report_date = pd.Timestamp(row.get("date") or row.get("reportDate"))
    except (TypeError, ValueError):
        return None
    if pd.isna(report_date):
        return None
    return report_date.year, report_date.quarter


def _prior_year_quarter(rows: list[dict]) -> Optional[dict]:
    if not rows:
        return None
    latest_period = _fiscal_period(rows[0])
    if latest_period is None:
        return None
    target_period = (latest_period[0] - 1, latest_period[1])
    return next(
        (row for row in rows[1:] if _fiscal_period(row) == target_period),
        None,
    )


def _trend_growth(trend: Any, period_names: Iterable[str]) -> Optional[float]:
    wanted = {name.lower() for name in period_names}
    for row in _dated_values(trend):
        if str(row.get("period") or "").lower() in wanted:
            return safe_decimal_rate(_first_present(row.get("growth"), row.get("earningsEstimateGrowth")))
    return None


def extract_fundamental_metrics(payload: dict) -> dict[str, Any]:
    """Extract one provider payload without silently coercing missing values."""
    general = payload.get("General") or {}
    highlights = payload.get("Highlights") or {}
    valuation = payload.get("Valuation") or {}
    shares = payload.get("SharesStats") or {}
    dividends = payload.get("SplitsDividends") or {}
    ratings = payload.get("AnalystRatings") or {}
    earnings = payload.get("Earnings") or {}
    financials = payload.get("Financials") or {}
    income = financials.get("Income_Statement") or {}
    balance = financials.get("Balance_Sheet") or {}
    cash_flow = financials.get("Cash_Flow") or {}
    quarterly_income = _dated_values(income.get("quarterly"))
    yearly_income = _dated_values(income.get("yearly"))
    quarterly_balance = _dated_values(balance.get("quarterly"))
    yearly_balance = _dated_values(balance.get("yearly"))
    quarterly_cash = _dated_values(cash_flow.get("quarterly"))
    yearly_cash = _dated_values(cash_flow.get("yearly"))

    latest_annual_income = yearly_income[0] if yearly_income else {}
    latest_balance = (
        quarterly_balance[0]
        if quarterly_balance
        else (yearly_balance[0] if yearly_balance else {})
    )
    latest_annual_cash = yearly_cash[0] if yearly_cash else {}

    provider_revenue_ttm = safe_float(highlights.get("RevenueTTM"))
    provider_gross_profit_ttm = safe_float(highlights.get("GrossProfitTTM"))
    quarterly_revenue_ttm = _sum_metric(quarterly_income, "totalRevenue", 0, 4)
    quarterly_gross_profit_ttm = _sum_metric(quarterly_income, "grossProfit", 0, 4)
    quarterly_operating_income_ttm = _sum_metric(quarterly_income, "operatingIncome", 0, 4)
    quarterly_net_income_ttm = _sum_metric(quarterly_income, "netIncome", 0, 4)
    annual_revenue = safe_float(latest_annual_income.get("totalRevenue"))
    annual_gross_profit = safe_float(latest_annual_income.get("grossProfit"))
    annual_operating_income = safe_float(latest_annual_income.get("operatingIncome"))
    annual_net_income = safe_float(latest_annual_income.get("netIncome"))
    revenue_ttm = _first_present(
        provider_revenue_ttm,
        quarterly_revenue_ttm,
        annual_revenue,
    )
    gross_margin = _first_present(
        safe_ratio(provider_gross_profit_ttm, provider_revenue_ttm),
        safe_ratio(quarterly_gross_profit_ttm, quarterly_revenue_ttm),
        safe_ratio(annual_gross_profit, annual_revenue),
    )
    operating_margin_fallback = _first_present(
        safe_ratio(
            quarterly_operating_income_ttm,
            quarterly_revenue_ttm,
        ),
        safe_ratio(
            annual_operating_income,
            annual_revenue,
        ),
    )
    net_margin_fallback = _first_present(
        safe_ratio(
            quarterly_net_income_ttm,
            quarterly_revenue_ttm,
        ),
        safe_ratio(
            annual_net_income,
            annual_revenue,
        ),
    )
    net_income_ttm = _first_present(quarterly_net_income_ttm, annual_net_income)
    fcf_ttm = _sum_metric(quarterly_cash, "freeCashFlow", 0, 4)
    if fcf_ttm is None:
        fcf_ttm = safe_float(latest_annual_cash.get("freeCashFlow"))

    market_cap = safe_float(highlights.get("MarketCapitalization"))
    cash = safe_float(_first_present(
        latest_balance.get("cashAndShortTermInvestments"),
        latest_balance.get("cashAndEquivalents"),
        latest_balance.get("cash"),
    ))
    current_assets = safe_float(latest_balance.get("totalCurrentAssets"))
    current_liabilities = safe_float(latest_balance.get("totalCurrentLiabilities"))
    inventory = safe_float(latest_balance.get("inventory"))
    equity = safe_float(latest_balance.get("totalStockholderEquity"))
    long_term_debt = safe_float(_first_present(
        latest_balance.get("longTermDebtTotal"),
        latest_balance.get("longTermDebt"),
    ))
    total_debt = safe_float(_first_present(
        latest_balance.get("shortLongTermDebtTotal"),
        latest_balance.get("totalDebt"),
        highlights.get("TotalDebt"),
    ))
    invested_capital = safe_float(latest_balance.get("netInvestedCapital"))
    ebit = _first_present(
        _sum_metric(quarterly_income, "ebit", 0, 4),
        _sum_metric(quarterly_income, "operatingIncome", 0, 4),
        safe_float(_first_present(
            latest_annual_income.get("ebit"),
            latest_annual_income.get("operatingIncome"),
        )),
    )
    quarterly_tax = _sum_metric(quarterly_income, "incomeTaxExpense", 0, 4)
    quarterly_pretax = _sum_metric(quarterly_income, "incomeBeforeTax", 0, 4)
    if quarterly_tax is not None and quarterly_pretax is not None:
        tax, pretax = quarterly_tax, quarterly_pretax
    else:
        tax = safe_float(latest_annual_income.get("incomeTaxExpense"))
        pretax = safe_float(latest_annual_income.get("incomeBeforeTax"))
    tax_rate = safe_ratio(tax, pretax, positive_denominator=False)
    if tax_rate is not None and not 0 <= tax_rate <= 1:
        tax_rate = None

    prior_year_revenue_quarter = _prior_year_quarter(quarterly_income)
    revenue_qoq = (
        _growth_with_positive_base(
            quarterly_income[0].get("totalRevenue"),
            prior_year_revenue_quarter.get("totalRevenue"),
        )
        if quarterly_income and prior_year_revenue_quarter is not None
        else None
    )
    revenue_ttm_previous = _sum_metric(quarterly_income, "totalRevenue", 4, 4)
    if provider_revenue_ttm is not None and revenue_ttm_previous is not None:
        sales_growth_ttm = _growth_with_positive_base(
            provider_revenue_ttm,
            revenue_ttm_previous,
        )
    elif quarterly_revenue_ttm is not None and revenue_ttm_previous is not None:
        sales_growth_ttm = _growth_with_positive_base(
            quarterly_revenue_ttm,
            revenue_ttm_previous,
        )
    else:
        sales_growth_ttm = _annual_change(
            yearly_income,
            "totalRevenue",
            1,
            cagr=False,
        )
    sales_growth_3yr = _annual_change(
        yearly_income,
        "totalRevenue",
        3,
        cagr=True,
    )
    sales_growth_5yr = _annual_change(
        yearly_income,
        "totalRevenue",
        5,
        cagr=True,
    )

    annual_eps = _dated_values(earnings.get("Annual"))
    quarterly_eps = _dated_values(earnings.get("History") or earnings.get("Quarterly"))
    eps_ttm = _sum_metric(quarterly_eps, "epsActual", 0, 4)
    eps_ttm_previous = _sum_metric(quarterly_eps, "epsActual", 4, 4)
    prior_year_eps_quarter = _prior_year_quarter(quarterly_eps)
    eps_qoq = (
        _growth_with_positive_base(
            quarterly_eps[0].get("epsActual"),
            prior_year_eps_quarter.get("epsActual"),
        )
        if quarterly_eps and prior_year_eps_quarter is not None
        else None
    )
    provider_eps_qoq = safe_decimal_rate(highlights.get("QuarterlyEarningsGrowthYOY"))
    comparison_eps = (
        safe_float(prior_year_eps_quarter.get("epsActual"))
        if prior_year_eps_quarter is not None
        else None
    )
    if comparison_eps is not None and comparison_eps <= 0:
        eps_growth_qoq = None
    else:
        eps_growth_qoq = _first_present(provider_eps_qoq, eps_qoq)
    eps_growth_3yr = _annual_change(
        annual_eps,
        "epsActual",
        3,
        cagr=True,
    )
    eps_growth_5yr = _annual_change(
        annual_eps,
        "epsActual",
        5,
        cagr=True,
    )

    ipo_date = general.get("IPODate")
    try:
        ipo_date = pd.Timestamp(ipo_date).date() if ipo_date else None
    except (TypeError, ValueError):
        ipo_date = None

    peg_ratio_raw = safe_float(highlights.get("PEGRatio"))
    nonpositive_revenue = revenue_ttm is not None and revenue_ttm <= 0
    provider_operating_margin = safe_decimal_rate(highlights.get("OperatingMarginTTM"))
    provider_net_margin = safe_decimal_rate(highlights.get("ProfitMargin"))
    if nonpositive_revenue:
        provider_operating_margin = None
        provider_net_margin = None

    comparison_revenue = (
        safe_float(prior_year_revenue_quarter.get("totalRevenue"))
        if prior_year_revenue_quarter is not None
        else None
    )
    provider_sales_qoq = safe_decimal_rate(highlights.get("QuarterlyRevenueGrowthYOY"))
    if comparison_revenue is not None and comparison_revenue <= 0:
        provider_sales_qoq = None

    payout_ratio = safe_decimal_rate(dividends.get("PayoutRatio"))
    earnings_basis = _first_present(
        net_income_ttm,
        safe_float(highlights.get("EarningsShare")),
    )
    if earnings_basis is not None and earnings_basis <= 0:
        payout_ratio = None

    provider_roe = safe_decimal_rate(highlights.get("ReturnOnEquityTTM"))
    if equity is not None and equity <= 0:
        provider_roe = None

    result = {
        "exchange": general.get("Exchange"),
        "country": general.get("CountryName") or general.get("CountryISO"),
        "ipo_date": ipo_date,
        "market_cap": market_cap,
        "pe_ratio": safe_float(_first_present(highlights.get("PERatio"), valuation.get("TrailingPE"))),
        "forward_pe": safe_float(valuation.get("ForwardPE")),
        "peg_ratio_raw": peg_ratio_raw,
        "peg_ratio": normalize_peg_ratio(peg_ratio_raw),
        "ps_ratio": None if nonpositive_revenue else safe_float(valuation.get("PriceSalesTTM")),
        "pb_ratio": (
            None
            if equity is not None and equity <= 0
            else safe_float(valuation.get("PriceBookMRQ"))
        ),
        "price_cash": safe_ratio(market_cap, cash),
        "price_fcf": safe_ratio(market_cap, fcf_ttm),
        "ev_ebitda": safe_float(valuation.get("EnterpriseValueEbitda")),
        "ev_sales": (
            None
            if nonpositive_revenue
            else safe_float(valuation.get("EnterpriseValueRevenue"))
        ),
        "dividend_yield": safe_decimal_rate(_first_present(
            highlights.get("DividendYield"),
            dividends.get("ForwardAnnualDividendYield"),
        )),
        "payout_ratio": payout_ratio,
        "short_float": safe_decimal_rate(shares.get("ShortPercentFloat")),
        "analyst_recommendation": safe_float(ratings.get("Rating")),
        "target_price": safe_float(_first_present(
            ratings.get("TargetPrice"),
            highlights.get("WallStreetTargetPrice"),
        )),
        "shares_outstanding": int(value) if (value := safe_float(shares.get("SharesOutstanding"))) is not None else None,
        "shares_float": int(value) if (value := safe_float(shares.get("SharesFloat"))) is not None else None,
        "roe": provider_roe,
        "roa": safe_decimal_rate(highlights.get("ReturnOnAssetsTTM")),
        "roic": safe_ratio(
            ebit * (1.0 - tax_rate) if ebit is not None and tax_rate is not None else None,
            invested_capital,
        ),
        "debt_to_equity": safe_ratio(total_debt, equity),
        "lt_debt_to_equity": safe_ratio(long_term_debt, equity),
        "current_ratio": safe_ratio(current_assets, current_liabilities),
        "quick_ratio": safe_ratio(
            current_assets - inventory if current_assets is not None and inventory is not None else None,
            current_liabilities,
        ),
        "fcf": fcf_ttm,
        "gross_margin": gross_margin,
        "operating_margin": _first_present(
            provider_operating_margin,
            operating_margin_fallback,
        ),
        "net_profit_margin": _first_present(
            provider_net_margin,
            net_margin_fallback,
        ),
        "sales_growth_qoq": _first_present(
            provider_sales_qoq,
            revenue_qoq,
        ),
        "sales_growth_ttm": sales_growth_ttm,
        "sales_growth_3yr": sales_growth_3yr,
        "sales_growth_5yr": sales_growth_5yr,
        "eps_growth_this_year": _trend_growth(earnings.get("Trend"), {"0y", "current year", "year"}),
        "eps_growth_next_year": _trend_growth(earnings.get("Trend"), {"+1y", "next year"}),
        "eps_growth_qoq": eps_growth_qoq,
        "eps_growth_ttm": _growth_with_positive_base(eps_ttm, eps_ttm_previous),
        "eps_growth_3yr": eps_growth_3yr,
        "eps_growth_5yr": eps_growth_5yr,
        "insider_ownership": safe_percentage_points(shares.get("PercentInsiders")),
        "institutional_ownership": safe_percentage_points(shares.get("PercentInstitutions")),
    }
    return dict(normalize_public_screener_values(result))


def classify_candlestick(rows: pd.DataFrame) -> Optional[str]:
    if rows.empty or any(column not in rows for column in ("open_adj", "high_adj", "low_adj", "close_adj")):
        return None
    current = rows.iloc[-1]
    open_, high, low, close = (safe_float(current[key]) for key in ("open_adj", "high_adj", "low_adj", "close_adj"))
    if any(value is None for value in (open_, high, low, close)) or high <= low:
        return None
    body = abs(close - open_)
    candle_range = high - low
    upper = high - max(open_, close)
    lower = min(open_, close) - low
    if body <= candle_range * 0.10:
        return "Doji"
    if len(rows) >= 3:
        first, middle = rows.iloc[-3], rows.iloc[-2]
        first_open, first_close = safe_float(first["open_adj"]), safe_float(first["close_adj"])
        middle_open, middle_close = safe_float(middle["open_adj"]), safe_float(middle["close_adj"])
        if all(value is not None for value in (first_open, first_close, middle_open, middle_close)):
            midpoint = (first_open + first_close) / 2
            if (
                first_close < first_open
                and abs(middle_close - middle_open) < abs(first_close - first_open) * 0.35
                and close > open_
                and close > midpoint
            ):
                return "Morning Star"
            if (
                first_close > first_open
                and abs(middle_close - middle_open) < abs(first_close - first_open) * 0.35
                and close < open_
                and close < midpoint
            ):
                return "Evening Star"
    if len(rows) >= 2:
        previous = rows.iloc[-2]
        prev_open, prev_close = safe_float(previous["open_adj"]), safe_float(previous["close_adj"])
        if prev_open is not None and prev_close is not None:
            if prev_close < prev_open and close > open_ and open_ <= prev_close and close >= prev_open:
                return "Bullish Engulfing"
            if prev_close > prev_open and close < open_ and open_ >= prev_close and close <= prev_open:
                return "Bearish Engulfing"
    if lower >= body * 2 and upper <= body * 0.35:
        return "Hammer"
    if upper >= body * 2 and lower <= body * 0.35:
        return "Inverted Hammer"
    if body >= candle_range * 0.90:
        return "Bullish Marubozu" if close > open_ else "Bearish Marubozu"
    return None


def _unavailable_price_metrics(reason: str) -> dict[str, Any]:
    return {
        **{field_name: None for field_name in PRICE_METRIC_FIELDS},
        "technical_quality": reason,
    }


def calculate_price_metrics(group: pd.DataFrame, benchmark_returns: Optional[pd.Series] = None) -> dict[str, Any]:
    rows = group.sort_values("date").copy()
    if rows.empty:
        return {}
    required_columns = {"open", "high", "low", "close", "adjusted_close", "volume"}
    if not required_columns.issubset(rows.columns):
        return _unavailable_price_metrics("invalid_ohlc")
    for column in required_columns:
        rows[column] = pd.to_numeric(rows[column], errors="coerce")

    complete_ohlc = rows[["open", "high", "low", "close"]].notna().all(axis=1)
    populated_ohlc = rows.loc[complete_ohlc, ["open", "high", "low", "close"]]
    if not populated_ohlc.empty:
        invalid_ohlc = (
            (populated_ohlc <= 0).any(axis=1)
            | (populated_ohlc["high"] < populated_ohlc[["open", "close", "low"]].max(axis=1))
            | (populated_ohlc["low"] > populated_ohlc[["open", "close", "high"]].min(axis=1))
        )
        if invalid_ohlc.any():
            return _unavailable_price_metrics("invalid_ohlc")

    paired_closes = rows[["close", "adjusted_close"]].notna().all(axis=1)
    if (
        (rows.loc[paired_closes, "close"] <= 0).any()
        or (rows.loc[paired_closes, "adjusted_close"] <= 0).any()
    ):
        return _unavailable_price_metrics("invalid_adjustment_factor")

    factor = rows["adjusted_close"] / rows["close"].replace(0, np.nan)
    factor = factor.replace([np.inf, -np.inf], np.nan)
    if (factor.dropna() <= 0).any():
        return _unavailable_price_metrics("invalid_adjustment_factor")
    for column in ("open", "high", "low", "close"):
        rows[f"{column}_adj"] = pd.to_numeric(rows[column], errors="coerce") * factor
    close = rows["close_adj"]
    returns = close.pct_change(fill_method=None)
    if (returns.dropna() > MAX_ADJUSTED_DAILY_RETURN).any() or (returns.dropna() < -1).any():
        return _unavailable_price_metrics("extreme_adjusted_return")

    def perf(periods: int) -> Optional[float]:
        if len(close.dropna()) <= periods:
            return None
        return _growth(close.iloc[-1], close.iloc[-periods - 1])

    year_start = date(rows.iloc[-1]["date"].year, 1, 1)
    ytd_rows = rows[rows["date"] >= year_start]
    prior_year_rows = rows[rows["date"] < year_start]
    ytd = (
        _growth(ytd_rows["close_adj"].iloc[-1], prior_year_rows["close_adj"].iloc[-1])
        if not ytd_rows.empty and not prior_year_rows.empty
        else None
    )
    ma20 = safe_float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else None
    ma50 = safe_float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
    ma200 = safe_float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
    rsi = ta.rsi(close, length=14)
    previous_close = safe_float(close.iloc[-2]) if len(close) >= 2 else None
    latest_open = safe_float(rows["open_adj"].iloc[-1])
    latest_close = safe_float(close.iloc[-1])
    high = rows["high_adj"]
    low = rows["low_adj"]
    daily_range = (high - low) / low.abs().replace(0, np.nan)

    def average_daily_range(periods: int) -> Optional[float]:
        window = daily_range.tail(periods).dropna()
        return safe_float(window.mean()) if len(window) == periods else None

    true_range = pd.concat(
        [(high - low), (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
        axis=1,
    ).max(axis=1, skipna=False)
    complete_inputs = (
        high.notna()
        & low.notna()
        & close.notna()
        & close.shift(1).notna()
    )
    true_range = true_range.where(complete_inputs)
    if high.iloc[:1].notna().all() and low.iloc[:1].notna().all() and close.iloc[:1].notna().all():
        true_range.iloc[0] = high.iloc[0] - low.iloc[0]
    invalid_positions = np.flatnonzero(true_range.isna().to_numpy())
    true_range_values = (
        true_range.iloc[invalid_positions[-1] + 1:]
        if len(invalid_positions)
        else true_range
    )
    atr = None
    if len(true_range_values) >= 14:
        atr = safe_float(true_range_values.iloc[:14].mean())
        for true_range_value in true_range_values.iloc[14:]:
            atr = safe_float(
                (atr * 13 + true_range_value) / 14
                if atr is not None
                else None
            )
    volume_window = pd.to_numeric(rows["volume"], errors="coerce").tail(63).dropna()
    average_volume = safe_float(volume_window.mean()) if len(volume_window) == 63 else None
    current_volume = safe_float(rows["volume"].iloc[-1])
    beta = None
    if benchmark_returns is not None:
        asset_returns = pd.Series(returns.values, index=pd.to_datetime(rows["date"])).dropna()
        aligned = pd.concat([asset_returns.rename("asset"), benchmark_returns.rename("benchmark")], axis=1).dropna().tail(252)
        if len(aligned) >= 126 and aligned["benchmark"].var() > 0:
            beta = safe_float(aligned.cov().loc["asset", "benchmark"] / aligned["benchmark"].var())

    result: dict[str, Any] = {
        "technical_quality": "ok",
        "ma20": ma20,
        "ma50": ma50,
        "ma200": ma200,
        "rsi_14": safe_float(rsi.iloc[-1]) if rsi is not None and not rsi.empty else None,
        "average_volume_3m": average_volume,
        "relative_volume": safe_ratio(current_volume, average_volume),
        "performance_1d": perf(1),
        "performance_1w": perf(5),
        "performance_1m": perf(21),
        "performance_3m": perf(63),
        "performance_6m": perf(126),
        "performance_ytd": ytd,
        "performance_1yr": perf(252),
        "volatility_1w": average_daily_range(5),
        "volatility_1m": average_daily_range(21),
        "gap": _growth(latest_open, previous_close),
        "change_from_open": _growth(latest_close, latest_open),
        "beta_1yr": beta,
        "atr_14": atr,
        "candlestick": classify_candlestick(rows),
    }
    for window, suffix in ((20, "20d"), (50, "50d"), (252, "52w")):
        high_window = high.tail(window)
        low_window = low.tail(window)
        if (
            len(rows) >= window
            and latest_close is not None
            and high_window.notna().sum() == window
            and low_window.notna().sum() == window
        ):
            rolling_high = safe_float(high_window.max())
            rolling_low = safe_float(low_window.min())
            result[f"high_{suffix}_rel"] = _growth(latest_close, rolling_high)
            result[f"low_{suffix}_rel"] = _growth(latest_close, rolling_low)
        else:
            result[f"high_{suffix}_rel"] = None
            result[f"low_{suffix}_rel"] = None
    return dict(normalize_public_screener_values(result))


def calculate_dividend_growth(actions: Iterable[tuple[date, Any]], as_of_date: date) -> dict[str, Optional[float]]:
    yearly: dict[int, float] = {}
    for ex_date, amount in actions:
        value = safe_float(amount)
        if ex_date <= as_of_date and value is not None and value > 0:
            yearly[ex_date.year] = yearly.get(ex_date.year, 0.0) + value
    last_session = date(as_of_date.year, 12, 31)
    while not is_us_market_session(last_session):
        last_session -= timedelta(days=1)
    latest_year = as_of_date.year if as_of_date >= last_session else as_of_date.year - 1
    current = yearly.get(latest_year)
    return {
        "dividend_growth_1yr": _growth(current, yearly.get(latest_year - 1)),
        "dividend_growth_3yr": _cagr(current, yearly.get(latest_year - 3), 3),
        "dividend_growth_5yr": _cagr(current, yearly.get(latest_year - 5), 5),
    }
