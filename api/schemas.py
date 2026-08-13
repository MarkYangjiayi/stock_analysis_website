from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field
from datetime import date, datetime

class StockProfileModel(BaseModel):
    ticker: str
    name: Optional[str] = None
    exchange: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    description: Optional[str] = None
    currency: Optional[str] = None
    last_updated: Optional[str] = None

class HistoricalDataPointModel(BaseModel):
    date: str
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None
    MA20: Optional[float] = None
    MA50: Optional[float] = None
    RSI: Optional[float] = None
    MACD: Optional[float] = None
    MACD_Signal: Optional[float] = None
    MACD_Hist: Optional[float] = None

class HistoricalFinancialPointModel(BaseModel):
    date: str
    revenue: float
    net_income: float
    gross_margin: float
    free_cash_flow: Optional[float] = None
    operating_margin: Optional[float] = None
    cash_and_short_term_investments: Optional[float] = None
    total_debt: Optional[float] = None
    stockholder_equity: Optional[float] = None
    debt_to_equity: Optional[float] = None
    shares_outstanding: Optional[float] = None
    price: Optional[float] = None

class TTMDataModel(BaseModel):
    revenue: float
    gross_profit: float
    net_income: float
    free_cash_flow: float
    roe: float

class BalanceSheetLatestModel(BaseModel):
    total_assets: float
    total_liabilities: float
    total_stockholder_equity: float
    shares_outstanding: float

class ValuationAssumptionsModel(BaseModel):
    fcf_growth_rate_5yr: float
    wacc: float
    perpetual_growth: float

class ValuationModel(BaseModel):
    dcf_intrinsic_value_per_share: float
    current_price: float
    margin_of_safety: float
    assumptions: ValuationAssumptionsModel

class FactorScoresModel(BaseModel):
    value: int
    quality: int
    growth: int
    health: int
    momentum: int

class ValuationMetricsModel(BaseModel):
    ttm: TTMDataModel
    balance_sheet_latest: BalanceSheetLatestModel
    valuation: ValuationModel
    factor_scores: FactorScoresModel
    data_quality_warnings: List[dict] = Field(default_factory=list)

class StockDataResponse(BaseModel):
    profile: StockProfileModel
    historical_data: List[HistoricalDataPointModel]
    historical_financials: List[HistoricalFinancialPointModel]
    valuation_metrics: Optional[ValuationMetricsModel] = None


class MarketSnapshotMetricModel(BaseModel):
    value: Optional[Any] = None
    unit: Literal[
        "currency",
        "integer",
        "multiple",
        "number",
        "percent",
        "ratio",
        "date",
        "text",
    ]
    source_date: Optional[date] = None
    unavailable_reason: Optional[str] = None
    secondary_value: Optional[float] = None
    secondary_unit: Optional[Literal["currency", "percent", "number"]] = None
    percentile: Optional[float] = None
    percentile_scope: Optional[Literal["industry", "sector"]] = None


class MarketSnapshotSourceDatesModel(BaseModel):
    price: Optional[date] = None
    screener: Optional[date] = None
    financials: Optional[date] = None
    provider: Optional[date] = None


class MarketSnapshotCoverageModel(BaseModel):
    available: int
    total: int
    ratio: float


class MarketSnapshotResponse(BaseModel):
    ticker: str
    currency: Optional[str] = None
    source_dates: MarketSnapshotSourceDatesModel
    coverage: MarketSnapshotCoverageModel
    metrics: Dict[str, MarketSnapshotMetricModel]


class PeerMultipleMetricModel(BaseModel):
    key: Literal[
        "pe_ratio",
        "forward_pe",
        "ps_ratio",
        "pb_ratio",
        "price_fcf",
        "ev_sales",
        "ev_ebitda",
    ]
    label: str
    format: Literal["multiple"]


class PeerMultipleTargetModel(BaseModel):
    ticker: str
    name: Optional[str] = None
    value: Optional[float] = None
    market_cap: Optional[float] = None
    sales_growth_ttm: Optional[float] = None
    raw_percentile: Optional[float] = None
    premium_to_median: Optional[float] = None


class PeerMultipleCohortModel(BaseModel):
    scope: Literal["industry", "sector"]
    name: Optional[str] = None
    member_count: int
    valid_count: int
    excluded_count: int
    minimum_observations: int


class PeerMultipleDistributionModel(BaseModel):
    mean: float
    median: float
    p10: float
    p25: float
    p75: float
    p90: float


class PeerMultipleMemberModel(BaseModel):
    ticker: str
    name: Optional[str] = None
    value: float
    market_cap: Optional[float] = None
    sales_growth_ttm: Optional[float] = None


class PeerMultiplesResponse(BaseModel):
    available: bool
    reason: Optional[Literal[
        "target_not_in_snapshot",
        "target_metric_unavailable",
        "insufficient_industry_coverage",
        "insufficient_sector_coverage",
    ]] = None
    metric: PeerMultipleMetricModel
    as_of_date: Optional[date] = None
    target: PeerMultipleTargetModel
    cohort: Optional[PeerMultipleCohortModel] = None
    distribution: Optional[PeerMultipleDistributionModel] = None
    peers: List[PeerMultipleMemberModel] = Field(default_factory=list)


class StockEventModel(BaseModel):
    id: str
    kind: Literal["earnings", "dividend"]
    status: Literal["upcoming", "reported"]
    title: str
    event_date: date
    period_end: Optional[date] = None
    payment_date: Optional[date] = None
    timing: Optional[str] = None
    eps_actual: Optional[float] = None
    eps_estimate: Optional[float] = None
    eps_difference: Optional[float] = None
    eps_surprise_percent: Optional[float] = None


class EarningsExpectationModel(BaseModel):
    period: str
    label: str
    period_end: date
    eps_average: Optional[float] = None
    eps_low: Optional[float] = None
    eps_high: Optional[float] = None
    eps_growth: Optional[float] = None
    revenue_average: Optional[float] = None
    revenue_low: Optional[float] = None
    revenue_high: Optional[float] = None
    revenue_growth: Optional[float] = None
    eps_analyst_count: Optional[int] = None
    revenue_analyst_count: Optional[int] = None
    eps_revisions_up_7d: Optional[int] = None
    eps_revisions_down_7d: Optional[int] = None
    eps_revisions_up_30d: Optional[int] = None
    eps_revisions_down_30d: Optional[int] = None
    eps_trend_current: Optional[float] = None
    eps_trend_7d: Optional[float] = None
    eps_trend_30d: Optional[float] = None


class EventsExpectationsResponse(BaseModel):
    ticker: str
    source: str
    as_of: Optional[datetime] = None
    available: bool
    next_event: Optional[StockEventModel] = None
    upcoming_events: List[StockEventModel] = Field(default_factory=list)
    recent_earnings: List[StockEventModel] = Field(default_factory=list)
    expectations: List[EarningsExpectationModel] = Field(default_factory=list)
    wall_street_target_price: Optional[float] = None
    dividend_yield: Optional[float] = None
    annual_dividend_per_share: Optional[float] = None
    data_quality_notes: List[str] = Field(default_factory=list)


class DecisionValuationScenarioInput(BaseModel):
    scenario: Literal["bear", "base", "bull"]
    fcf_growth_rate: float
    wacc: float
    perpetual_growth: float


class ValuationScenariosRequest(BaseModel):
    scenarios: List[DecisionValuationScenarioInput] = Field(min_length=3, max_length=3)


class EarningsQualityAnalysisRequest(BaseModel):
    period_end: date
    period_type: Literal["annual", "quarterly"]


class PersonalWatchlistRequest(BaseModel):
    tickers: List[str] = Field(default_factory=list, max_length=100)


class MarketOverviewMeta(BaseModel):
    universe: Literal["SP500", "RUSSELL2000", "SP500_RUSSELL2000"]
    period: Literal["3m", "6m", "1y"]
    as_of_date: date
    expected_as_of_date: date
    published_at: datetime
    stale: bool
    membership_mode: Literal["point_in_time"]
    data_complete: bool
    warnings: List[str] = Field(default_factory=list)


class MarketBenchmarkSeries(BaseModel):
    ticker: str
    absolute_index: List[float]


class MarketSectorTrend(BaseModel):
    ticker: str
    label: str
    absolute_index: List[float]
    relative_to_spy_index: List[float]


class MarketBreadthSeries(BaseModel):
    pct_above_ma20: List[Optional[float]]
    pct_above_ma50: List[Optional[float]]
    pct_above_ma200: List[Optional[float]]
    net_advances_pct: List[Optional[float]]
    new_high_low_pct: List[Optional[float]]
    new_high_pct: List[Optional[float]]
    new_low_pct: List[Optional[float]]
    mcclellan: List[Optional[float]]
    dispersion_1d: List[Optional[float]]
    dispersion_20d: List[Optional[float]]
    member_count: List[int]
    price_coverage_pct: List[Optional[float]]


class MarketOverviewResponse(BaseModel):
    meta: MarketOverviewMeta
    dates: List[date]
    benchmark: MarketBenchmarkSeries
    sector_trends: List[MarketSectorTrend]
    rsp_spy_index: List[float]
    breadth: MarketBreadthSeries


class FactorComputeRequest(BaseModel):
    as_of_date: date


class FactorResearchRequest(BaseModel):
    start_date: date
    end_date: date
    factor_name: str = "composite"
    factor_version: str = "lfq-v1"
    horizon_days: int = Field(21, ge=1, le=252)
    quantiles: int = Field(5, ge=2, le=10)


class BacktestRequest(BaseModel):
    name: str = "Low Frequency Multi-Factor"
    start_date: date
    end_date: date
    factor_name: str = "composite"
    factor_version: str = "lfq-v1"
    universe: str = "SP500"
    benchmark: str = "SPY.US"
    rebalance_frequency: Literal["weekly", "monthly", "all"] = "monthly"
    signal_lag_days: int = Field(1, ge=1, le=10)
    top_n: int = Field(30, ge=2, le=500)
    max_position_weight: float = Field(0.05, gt=0, le=1)
    max_sector_weight: float = Field(0.30, gt=0, le=1)
    transaction_cost_bps: float = Field(5.0, ge=0, le=500)
    slippage_bps: float = Field(5.0, ge=0, le=500)
    require_point_in_time_universe: bool = True
    missing_price_policy: Literal["fail", "liquidate_last"] = "fail"


class AnomalyNewsSource(BaseModel):
    title: str
    link: str
    pub_date: datetime
    summary: str
    publisher: str


class AnomalyReportModel(BaseModel):
    ticker: str
    company_name: str
    company_description: Optional[str] = None
    market_cap: Optional[float] = None
    date: date
    quote_timestamp: datetime
    price_change: float
    ai_analysis: str
    attribution_status: Literal[
        "completed",
        "no_news",
        "timed_out",
        "news_unavailable",
        "attribution_unavailable",
    ]
    news: List[AnomalyNewsSource] = Field(default_factory=list)
    top_news_links: List[str] = Field(default_factory=list)


class AnomalyScanResponse(BaseModel):
    id: int
    trigger: str
    status: Literal["queued", "running", "completed", "failed"]
    requested_limit: int
    threshold_pct: float
    universe_as_of: Optional[date] = None
    quote_as_of: Optional[datetime] = None
    results: List[AnomalyReportModel] = Field(default_factory=list)
    error_message: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
