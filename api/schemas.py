from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from datetime import date

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

class StockDataResponse(BaseModel):
    profile: StockProfileModel
    historical_data: List[HistoricalDataPointModel]
    historical_financials: List[HistoricalFinancialPointModel]
    valuation_metrics: Optional[ValuationMetricsModel] = None


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
    universe: str = "SP500_RUSSELL2000"
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
