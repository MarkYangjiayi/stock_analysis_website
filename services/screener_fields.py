"""Single source of truth for screener query and UI metadata."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from models import StockScreenerSnapshot
from services.universe import SCREENER_INDEX_OPTIONS


NUMERIC_OPERATORS = ("lt", "lte", "gt", "gte", "between")
ENUM_OPERATORS = ("eq", "in")
_DEFAULT_FINVIZ_FIELD = object()


@dataclass(frozen=True)
class ScreenerField:
    id: str
    label: str
    category: str
    type: str = "number"
    unit: str = "number"
    operators: tuple[str, ...] = NUMERIC_OPERATORS
    column: Optional[str] = None
    finviz_field: Optional[str] = None
    presets: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    options: tuple[dict[str, str], ...] = field(default_factory=tuple)
    description: Optional[str] = None
    default_column: bool = False
    result_column: bool = True

    def metadata(self) -> dict[str, Any]:
        data = asdict(self)
        data["operators"] = list(self.operators)
        data["presets"] = list(self.presets)
        data["options"] = list(self.options)
        data.pop("column", None)
        return data


def _number(
    field_id: str,
    label: str,
    category: str,
    *,
    unit: str = "number",
    finviz: Any = _DEFAULT_FINVIZ_FIELD,
    presets: tuple[dict[str, Any], ...] = (),
    description: Optional[str] = None,
    default: bool = False,
) -> ScreenerField:
    return ScreenerField(
        id=field_id,
        label=label,
        category=category,
        unit=unit,
        column=field_id,
        finviz_field=label if finviz is _DEFAULT_FINVIZ_FIELD else finviz,
        presets=presets,
        description=description,
        default_column=default,
    )


def _enum(
    field_id: str,
    label: str,
    category: str,
    options: tuple[tuple[str, str], ...] = (),
    *,
    column: Optional[str] = None,
    finviz: Any = _DEFAULT_FINVIZ_FIELD,
    description: Optional[str] = None,
    result_column: bool = True,
) -> ScreenerField:
    return ScreenerField(
        id=field_id,
        label=label,
        category=category,
        type="enum",
        unit="text",
        operators=ENUM_OPERATORS,
        column=column or field_id,
        finviz_field=label if finviz is _DEFAULT_FINVIZ_FIELD else finviz,
        options=tuple({"value": value, "label": option_label} for value, option_label in options),
        description=description,
        result_column=result_column,
    )


PERCENT_PRESETS = (
    {"label": "Over 10%", "operator": "gte", "value": 0.10},
    {"label": "Over 20%", "operator": "gte", "value": 0.20},
    {"label": "Under 0%", "operator": "lt", "value": 0},
)
RATIO_PRESETS = (
    {"label": "Under 1", "operator": "lte", "value": 1},
    {"label": "Under 2", "operator": "lte", "value": 2},
    {"label": "Over 3", "operator": "gte", "value": 3},
)
PEG_PRESETS = (
    {"label": "Below 1", "operator": "lt", "value": 1},
    {"label": "Below 2", "operator": "lt", "value": 2},
    {"label": "3 or more", "operator": "gte", "value": 3},
)


FIELD_DEFINITIONS = [
    _enum("exchange", "Exchange", "Descriptive"),
    _enum(
        "index",
        "Index",
        "Descriptive",
        SCREENER_INDEX_OPTIONS,
        column=None,
        result_column=False,
    ),
    _enum("sector", "Sector", "Descriptive"),
    _enum("industry", "Industry", "Descriptive"),
    _enum("country", "Country", "Descriptive"),
    _number(
        "market_cap",
        "Market Cap",
        "Descriptive",
        unit="currency",
        presets=(
            {"label": "Mega ($200B+)", "operator": "gte", "value": 200_000_000_000},
            {"label": "Large ($10B–$200B)", "operator": "between", "value": [10_000_000_000, 200_000_000_000]},
            {"label": "Mid ($2B–$10B)", "operator": "between", "value": [2_000_000_000, 10_000_000_000]},
            {"label": "Small ($300M–$2B)", "operator": "between", "value": [300_000_000, 2_000_000_000]},
        ),
        default=True,
    ),
    _number(
        "dividend_yield",
        "Dividend Yield",
        "Descriptive",
        unit="percent",
        presets=PERCENT_PRESETS,
        description="Zero is retained for companies that do not currently pay a dividend.",
    ),
    _number(
        "short_float",
        "Short Float",
        "Descriptive",
        unit="percent",
        presets=PERCENT_PRESETS,
        description="Values above 100% can be valid when short interest exceeds the reported float.",
    ),
    _number(
        "analyst_recommendation",
        "Analyst Recommendation",
        "Descriptive",
        presets=RATIO_PRESETS,
        description="Finviz-style scale from 1 (Strong Buy) to 5 (Strong Sell); values outside that range are unavailable.",
    ),
    _number("average_volume_3m", "Average Volume (3M)", "Descriptive", unit="integer"),
    _number("relative_volume", "Relative Volume", "Descriptive", presets=RATIO_PRESETS),
    _number("volume", "Current Volume", "Descriptive", unit="integer", default=True),
    _number("close", "Price", "Descriptive", unit="currency", finviz="Price", default=True),
    _number(
        "target_price",
        "Target Price",
        "Descriptive",
        unit="currency",
        description="Zero or negative provider targets are treated as unavailable.",
    ),
    ScreenerField(
        id="ipo_date",
        label="IPO Date",
        category="Descriptive",
        type="date",
        unit="date",
        operators=("eq", "lt", "lte", "gt", "gte", "between"),
        column="ipo_date",
        finviz_field="IPO Date",
    ),
    _number("shares_outstanding", "Shares Outstanding", "Descriptive", unit="integer"),
    _number("shares_float", "Float", "Descriptive", unit="integer"),

    _number(
        "pe_ratio",
        "P/E",
        "Fundamental",
        presets=RATIO_PRESETS,
        description="Only positive earnings multiples are comparable; loss-making or zero values display as unavailable.",
        default=True,
    ),
    _number(
        "forward_pe",
        "Forward P/E",
        "Fundamental",
        presets=RATIO_PRESETS,
        description="Only positive forecast earnings multiples are exposed for screening.",
    ),
    _number(
        "peg_ratio",
        "PEG (5Y Expected)",
        "Fundamental",
        finviz="PEG",
        presets=PEG_PRESETS,
        description=(
            "Provider-supplied PEG using five-year expected earnings growth. "
            "Values at or below 0 are treated as unavailable because PEG is not "
            "meaningful when earnings or expected growth is non-positive."
        ),
    ),
    _number(
        "ps_ratio",
        "P/S",
        "Fundamental",
        presets=RATIO_PRESETS,
        description="Requires positive revenue; zero and provider sentinel values display as unavailable.",
    ),
    _number(
        "pb_ratio",
        "P/B",
        "Fundamental",
        presets=RATIO_PRESETS,
        description="Requires positive book equity; non-positive multiples display as unavailable.",
    ),
    _number(
        "price_cash",
        "Price/Cash",
        "Fundamental",
        presets=RATIO_PRESETS,
        description="Requires a positive cash balance and a positive multiple.",
    ),
    _number(
        "price_fcf",
        "Price/Free Cash Flow",
        "Fundamental",
        presets=RATIO_PRESETS,
        description="Requires positive free cash flow; cash-burning companies display as unavailable.",
    ),
    _number(
        "ev_ebitda",
        "EV/EBITDA",
        "Fundamental",
        presets=RATIO_PRESETS,
        description="Only positive EV/EBITDA values are exposed for peer comparison.",
    ),
    _number(
        "ev_sales",
        "EV/Sales",
        "Fundamental",
        presets=RATIO_PRESETS,
        description="Requires positive sales and a positive multiple; provider sentinel values are unavailable.",
    ),
    _number("dividend_growth_1yr", "Dividend Growth (1Y)", "Fundamental", unit="percent", finviz="Dividend Growth", presets=PERCENT_PRESETS),
    _number("dividend_growth_3yr", "Dividend Growth (3Y)", "Fundamental", unit="percent", finviz="Dividend Growth", presets=PERCENT_PRESETS),
    _number("dividend_growth_5yr", "Dividend Growth (5Y)", "Fundamental", unit="percent", finviz="Dividend Growth", presets=PERCENT_PRESETS),
    _number("eps_growth_this_year", "EPS Growth This Year", "Fundamental", unit="percent", presets=PERCENT_PRESETS),
    _number("eps_growth_next_year", "EPS Growth Next Year", "Fundamental", unit="percent", presets=PERCENT_PRESETS),
    _number("eps_growth_qoq", "EPS Growth QoQ", "Fundamental", unit="percent", presets=PERCENT_PRESETS),
    _number("eps_growth_ttm", "EPS Growth TTM", "Fundamental", unit="percent", presets=PERCENT_PRESETS),
    _number("eps_growth_3yr", "EPS Growth Past 3Y", "Fundamental", unit="percent", presets=PERCENT_PRESETS),
    _number("eps_growth_5yr", "EPS Growth Past 5Y", "Fundamental", unit="percent", presets=PERCENT_PRESETS),
    _number(
        "sales_growth_qoq",
        "Sales Growth QoQ",
        "Fundamental",
        unit="percent",
        presets=PERCENT_PRESETS,
        description="Growth is unavailable when the comparison-period revenue is non-positive.",
    ),
    _number(
        "sales_growth_ttm",
        "Sales Growth TTM",
        "Fundamental",
        unit="percent",
        presets=PERCENT_PRESETS,
        description="Growth is unavailable when the prior TTM revenue base is non-positive.",
    ),
    _number("sales_growth_3yr", "Sales Growth Past 3Y", "Fundamental", unit="percent", presets=PERCENT_PRESETS),
    _number("sales_growth_5yr", "Sales Growth Past 5Y", "Fundamental", unit="percent", presets=PERCENT_PRESETS, default=True),
    _number("roa", "Return on Assets", "Fundamental", unit="percent", presets=PERCENT_PRESETS),
    _number(
        "roe",
        "Return on Equity",
        "Fundamental",
        unit="percent",
        presets=PERCENT_PRESETS,
        description="Negative ROE is retained when equity is positive; non-positive equity makes ROE unavailable.",
        default=True,
    ),
    _number("roic", "Return on Invested Capital", "Fundamental", unit="percent", presets=PERCENT_PRESETS),
    _number(
        "current_ratio",
        "Current Ratio",
        "Fundamental",
        presets=RATIO_PRESETS,
        description="Negative ratios violate the liquidity-ratio definition and display as unavailable.",
    ),
    _number(
        "quick_ratio",
        "Quick Ratio",
        "Fundamental",
        presets=RATIO_PRESETS,
        description="Negative ratios violate the liquidity-ratio definition and display as unavailable.",
    ),
    _number(
        "lt_debt_to_equity",
        "LT Debt/Equity",
        "Fundamental",
        presets=RATIO_PRESETS,
        description="Requires positive shareholder equity; negative ratios are unavailable.",
    ),
    _number(
        "debt_to_equity",
        "Debt/Equity",
        "Fundamental",
        presets=RATIO_PRESETS,
        description="Requires positive shareholder equity; negative ratios are unavailable.",
        default=True,
    ),
    _number(
        "gross_margin",
        "Gross Margin",
        "Fundamental",
        unit="percent",
        presets=PERCENT_PRESETS,
        description="Negative margins are retained when revenue is positive; zero or negative revenue makes the margin unavailable.",
        default=True,
    ),
    _number(
        "operating_margin",
        "Operating Margin",
        "Fundamental",
        unit="percent",
        presets=PERCENT_PRESETS,
        description="Negative margins are valid; the field is unavailable when revenue is non-positive.",
    ),
    _number(
        "net_profit_margin",
        "Net Profit Margin",
        "Fundamental",
        unit="percent",
        presets=PERCENT_PRESETS,
        description="Negative margins are valid; the field is unavailable when revenue is non-positive.",
    ),
    _number(
        "payout_ratio",
        "Payout Ratio",
        "Fundamental",
        unit="percent",
        presets=PERCENT_PRESETS,
        description="Zero is valid for profitable non-payers; the ratio is unavailable with non-positive earnings.",
    ),
    _number("insider_ownership", "Insider Ownership", "Fundamental", unit="percent", presets=PERCENT_PRESETS),
    _number(
        "institutional_ownership",
        "Institutional Ownership",
        "Fundamental",
        unit="percent",
        presets=PERCENT_PRESETS,
        description="Values above 100% can occur because filings use different report dates and are not automatically capped.",
    ),
    _number("fcf", "Free Cash Flow", "Fundamental", unit="currency", finviz=None),

    _enum(
        "technical_quality",
        "Technical Data Quality",
        "Technical",
        (
            ("ok", "OK"),
            ("invalid_ohlc", "Invalid OHLC"),
            ("invalid_adjustment_factor", "Invalid adjustment factor"),
            ("extreme_adjusted_return", "Extreme adjusted return"),
        ),
        finviz=None,
        description="Price-derived fields are blank when adjusted-price validation quarantines the symbol.",
    ),
    _number("performance_1d", "Performance (Day)", "Technical", unit="percent", finviz="Performance", presets=PERCENT_PRESETS),
    _number("performance_1w", "Performance (Week)", "Technical", unit="percent", finviz="Performance 2", presets=PERCENT_PRESETS),
    _number("performance_1m", "Performance (Month)", "Technical", unit="percent", finviz="Performance", presets=PERCENT_PRESETS),
    _number("performance_3m", "Performance (Quarter)", "Technical", unit="percent", finviz="Performance", presets=PERCENT_PRESETS),
    _number("performance_6m", "Performance (Half Year)", "Technical", unit="percent", finviz="Performance", presets=PERCENT_PRESETS),
    _number("performance_ytd", "Performance (YTD)", "Technical", unit="percent", finviz="Performance", presets=PERCENT_PRESETS),
    _number("performance_1yr", "Performance (Year)", "Technical", unit="percent", finviz="Performance", presets=PERCENT_PRESETS),
    _number("volatility_1w", "Volatility (Week)", "Technical", unit="percent", finviz="Volatility", presets=PERCENT_PRESETS),
    _number("volatility_1m", "Volatility (Month)", "Technical", unit="percent", finviz="Volatility", presets=PERCENT_PRESETS),
    _number("rsi_14", "RSI (14)", "Technical", presets=(
        {"label": "Oversold (<30)", "operator": "lt", "value": 30},
        {"label": "Overbought (>70)", "operator": "gt", "value": 70},
    )),
    _number("gap", "Gap", "Technical", unit="percent", presets=PERCENT_PRESETS),
    ScreenerField(
        id="change",
        label="Change",
        category="Technical",
        unit="percent",
        column="performance_1d",
        finviz_field="Change",
        presets=PERCENT_PRESETS,
    ),
    _number("ma20", "20-Day SMA", "Technical", unit="currency", finviz="20-Day Simple Moving Average"),
    _number("ma50", "50-Day SMA", "Technical", unit="currency", finviz="50-Day Simple Moving Average"),
    _number("ma200", "200-Day SMA", "Technical", unit="currency", finviz="200-Day Simple Moving Average"),
    _enum(
        "price_vs_ma20",
        "Price vs SMA20",
        "Technical",
        (("above", "Price above SMA20"), ("below", "Price below SMA20")),
        column=None,
        finviz="20-Day Simple Moving Average",
        result_column=False,
    ),
    _enum(
        "price_vs_ma50",
        "Price vs SMA50",
        "Technical",
        (("above", "Price above SMA50"), ("below", "Price below SMA50")),
        column=None,
        finviz="50-Day Simple Moving Average",
        result_column=False,
    ),
    _enum(
        "price_vs_ma200",
        "Price vs SMA200",
        "Technical",
        (("above", "Price above SMA200"), ("below", "Price below SMA200")),
        column=None,
        finviz="200-Day Simple Moving Average",
        result_column=False,
    ),
    _number("change_from_open", "Change from Open", "Technical", unit="percent", presets=PERCENT_PRESETS),
    _number("high_20d_rel", "20-Day High Distance", "Technical", unit="percent", finviz="20-Day High/Low"),
    _number("low_20d_rel", "20-Day Low Distance", "Technical", unit="percent", finviz="20-Day High/Low"),
    _number("high_50d_rel", "50-Day High Distance", "Technical", unit="percent", finviz="50-Day High/Low"),
    _number("low_50d_rel", "50-Day Low Distance", "Technical", unit="percent", finviz="50-Day High/Low"),
    _number("high_52w_rel", "52-Week High Distance", "Technical", unit="percent", finviz="52-Week High/Low"),
    _number("low_52w_rel", "52-Week Low Distance", "Technical", unit="percent", finviz="52-Week High/Low"),
    _enum(
        "candlestick",
        "Candlestick",
        "Technical",
        tuple((value, value) for value in (
            "Doji", "Hammer", "Inverted Hammer", "Bullish Engulfing",
            "Bearish Engulfing", "Morning Star", "Evening Star",
            "Bullish Marubozu", "Bearish Marubozu",
        )),
    ),
    _number("beta_1yr", "Beta", "Technical"),
    _number("atr_14", "Average True Range", "Technical", unit="currency"),
]

FIELD_MAP = {definition.id: definition for definition in FIELD_DEFINITIONS}
MODEL_FIELD_MAP = {
    definition.id: getattr(StockScreenerSnapshot, definition.column)
    for definition in FIELD_DEFINITIONS
    if definition.column and hasattr(StockScreenerSnapshot, definition.column)
}
DEFAULT_COLUMNS = [
    "ticker", "name", "sector", "market_cap", "close", "pe_ratio",
    "roe", "debt_to_equity", "gross_margin", "sales_growth_5yr",
]
SUPPORTED_FINVIZ_FIELDS = len(
    {
        definition.finviz_field
        for definition in FIELD_DEFINITIONS
        if definition.finviz_field
    }
)
