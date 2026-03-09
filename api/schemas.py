from typing import List, Optional, Any, Dict
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
