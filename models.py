import os
from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Optional, Any, List

from sqlalchemy import (
    Boolean,
    Float,
    String,
    Integer,
    Numeric,
    DateTime,
    Date,
    BigInteger,
    ForeignKey,
    Index,
    UniqueConstraint,
    JSON,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from core.time_utils import utc_now



# ------------------------------------------------------------------------
# 数据库模型 (Database Models)
# ------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass

class Ticker(Base):
    """
    1. tickers (股票基础信息表)
    """
    __tablename__ = "tickers"

    ticker: Mapped[str] = mapped_column(String, primary_key=True, index=True, doc="股票代码 (例: AAPL.US)")
    name: Mapped[Optional[str]] = mapped_column(String)
    exchange: Mapped[Optional[str]] = mapped_column(String)
    sector: Mapped[Optional[str]] = mapped_column(String)
    industry: Mapped[Optional[str]] = mapped_column(String)
    description: Mapped[Optional[str]] = mapped_column(String)
    currency: Mapped[Optional[str]] = mapped_column(String)
    last_updated: Mapped[Optional[datetime]] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, doc="基础信息最后同步时间")

    # 关联关系
    daily_prices: Mapped[List["DailyPrice"]] = relationship(back_populates="ticker_info", cascade="all, delete-orphan")
    financial_statements: Mapped[List["FinancialStatement"]] = relationship(back_populates="ticker_info", cascade="all, delete-orphan")


class DailyPrice(Base):
    """
    2. daily_prices (日 K 线历史行情表)
    """
    __tablename__ = "daily_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(ForeignKey("tickers.ticker"), index=True)
    date: Mapped[dt_date] = mapped_column(Date, index=True)
    
    open: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    high: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    low: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    close: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    adjusted_close: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    volume: Mapped[Optional[int]] = mapped_column(BigInteger)

    # 联合唯一索引
    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uix_daily_prices_ticker_date"),
    )

    ticker_info: Mapped["Ticker"] = relationship(back_populates="daily_prices")


class RRGPriceSnapshot(Base):
    """Immutable effective-close history for one published RRG pipeline run."""

    __tablename__ = "rrg_price_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_run_id: Mapped[int] = mapped_column(
        ForeignKey("pipeline_runs.id"),
    )
    ticker: Mapped[str] = mapped_column(
        ForeignKey("tickers.ticker"),
    )
    date: Mapped[dt_date] = mapped_column(Date)
    close: Mapped[Decimal] = mapped_column(Numeric)

    __table_args__ = (
        UniqueConstraint(
            "pipeline_run_id",
            "ticker",
            "date",
            name="uix_rrg_snapshot_run_ticker_date",
        ),
    )


class MarketBreadthSnapshot(Base):
    """Immutable point-in-time market breadth for one published pipeline run."""

    __tablename__ = "market_breadth_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"))
    universe: Mapped[str] = mapped_column(String)
    date: Mapped[dt_date] = mapped_column(Date)

    member_count: Mapped[int] = mapped_column(Integer)
    price_count: Mapped[int] = mapped_column(Integer)
    return_count: Mapped[int] = mapped_column(Integer)
    advances: Mapped[int] = mapped_column(Integer)
    declines: Mapped[int] = mapped_column(Integer)
    unchanged: Mapped[int] = mapped_column(Integer)

    ma20_eligible: Mapped[int] = mapped_column(Integer)
    above_ma20: Mapped[int] = mapped_column(Integer)
    ma50_eligible: Mapped[int] = mapped_column(Integer)
    above_ma50: Mapped[int] = mapped_column(Integer)
    ma200_eligible: Mapped[int] = mapped_column(Integer)
    above_ma200: Mapped[int] = mapped_column(Integer)

    high_low_eligible: Mapped[int] = mapped_column(Integer)
    new_high_count: Mapped[int] = mapped_column(Integer)
    new_low_count: Mapped[int] = mapped_column(Integer)
    dispersion_1d: Mapped[Optional[float]] = mapped_column(Float)

    __table_args__ = (
        Index(
            "ix_market_breadth_snapshots_run_universe_date",
            "pipeline_run_id",
            "universe",
            "date",
            unique=True,
        ),
    )


class FinancialStatement(Base):
    """
    3. financial_statements (财务报表表)
    """
    __tablename__ = "financial_statements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(ForeignKey("tickers.ticker"), index=True)
    fiscal_date: Mapped[dt_date] = mapped_column(Date)
    period: Mapped[str] = mapped_column(String, doc="Quarterly/Yearly")
    
    # 核心指标独立列
    revenue: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    net_income: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    
    # 完整原始报表存入 JSON
    income_statement: Mapped[Optional[Any]] = mapped_column(JSON)
    balance_sheet: Mapped[Optional[Any]] = mapped_column(JSON)
    cash_flow: Mapped[Optional[Any]] = mapped_column(JSON)

    # 联合唯一索引
    __table_args__ = (
        UniqueConstraint("ticker", "fiscal_date", "period", name="uix_financial_statements_ticker_date_period"),
    )

    ticker_info: Mapped["Ticker"] = relationship(back_populates="financial_statements")


class ComputedMetric(Base):
    """
    4. computed_metrics (预计算基本面指标表)
    """
    __tablename__ = "computed_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(ForeignKey("tickers.ticker"), index=True)
    date: Mapped[dt_date] = mapped_column(Date)
    
    pe_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    pb_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    roe: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    debt_to_equity: Mapped[Optional[Decimal]] = mapped_column(Numeric)

    # 根据业务逻辑通常对 ticker 和 date 有唯一约束
    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uix_computed_metrics_ticker_date"),
    )


class StockScreenerSnapshot(Base):
    """
    5. stock_screener_snapshot (全市场横截面数据快照表)
    此表专为 Stock Screener 构建，通过 Bulk API 后台任务每日清洗注入。
    """
    __tablename__ = "stock_screener_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    date: Mapped[dt_date] = mapped_column(Date, index=True)
    
    # 基础与分类
    name: Mapped[Optional[str]] = mapped_column(String)
    exchange: Mapped[Optional[str]] = mapped_column(String, index=True)
    sector: Mapped[Optional[str]] = mapped_column(String, index=True)
    industry: Mapped[Optional[str]] = mapped_column(String, index=True)
    country: Mapped[Optional[str]] = mapped_column(String, index=True)
    ipo_date: Mapped[Optional[dt_date]] = mapped_column(Date)
    
    # 基本面指标
    market_cap: Mapped[Optional[Decimal]] = mapped_column(Numeric, index=True)
    pe_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    pb_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    dividend_yield: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    short_float: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    analyst_recommendation: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    target_price: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    shares_outstanding: Mapped[Optional[int]] = mapped_column(BigInteger)
    shares_float: Mapped[Optional[int]] = mapped_column(BigInteger)
    roe: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    debt_to_equity: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    fcf: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    gross_margin: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    sales_growth_5yr: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    forward_pe: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    peg_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    ps_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    price_cash: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    price_fcf: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    ev_ebitda: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    ev_sales: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    dividend_growth_1yr: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    dividend_growth_3yr: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    dividend_growth_5yr: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    eps_growth_this_year: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    eps_growth_next_year: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    eps_growth_qoq: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    eps_growth_ttm: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    eps_growth_3yr: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    eps_growth_5yr: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    sales_growth_qoq: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    sales_growth_ttm: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    sales_growth_3yr: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    roa: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    roic: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    current_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    quick_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    lt_debt_to_equity: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    operating_margin: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    net_profit_margin: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    payout_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    insider_ownership: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    institutional_ownership: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    
    # 技术面指标 (通过近60天K线运算)
    close: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    volume: Mapped[Optional[int]] = mapped_column(BigInteger)
    average_volume_3m: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    relative_volume: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    ma20: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    ma50: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    ma200: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    rsi_14: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    performance_1d: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    performance_1w: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    performance_1m: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    performance_3m: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    performance_6m: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    performance_ytd: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    performance_1yr: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    volatility_1w: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    volatility_1m: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    gap: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    change_from_open: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    high_20d_rel: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    low_20d_rel: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    high_50d_rel: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    low_50d_rel: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    high_52w_rel: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    low_52w_rel: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    beta_1yr: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    atr_14: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    candlestick: Mapped[Optional[str]] = mapped_column(String)

    # 联合唯一索引支持 UPSERT
    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uix_screener_snapshot_ticker_date"),
    )


class AnomalyScanRun(Base):
    """Durable state and result payload for one market anomaly scan."""

    __tablename__ = "anomaly_scan_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trigger: Mapped[str] = mapped_column(String, default="manual", index=True)
    status: Mapped[str] = mapped_column(String, default="queued", index=True)
    active_key: Mapped[Optional[str]] = mapped_column(String, unique=True)
    owner_token: Mapped[Optional[str]] = mapped_column(String, index=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        index=True,
    )
    requested_limit: Mapped[int] = mapped_column(Integer, default=5)
    threshold_pct: Mapped[float] = mapped_column(Float, default=4.0)
    universe_as_of: Mapped[Optional[dt_date]] = mapped_column(Date, index=True)
    quote_as_of: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    results: Mapped[Optional[Any]] = mapped_column(JSON)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class SecurityMaster(Base):
    """Canonical security identity independent of a vendor ticker spelling."""
    __tablename__ = "security_master"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_ticker: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[Optional[str]] = mapped_column(String)
    exchange: Mapped[Optional[str]] = mapped_column(String, index=True)
    asset_type: Mapped[Optional[str]] = mapped_column(String, index=True)
    currency: Mapped[Optional[str]] = mapped_column(String)
    country: Mapped[Optional[str]] = mapped_column(String)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
    delisted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class SymbolHistory(Base):
    __tablename__ = "symbol_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("security_master.id"), index=True)
    symbol: Mapped[str] = mapped_column(String, index=True)
    exchange: Mapped[Optional[str]] = mapped_column(String)
    valid_from: Mapped[dt_date] = mapped_column(Date, index=True)
    valid_to: Mapped[Optional[dt_date]] = mapped_column(Date, index=True)
    source: Mapped[str] = mapped_column(String, default="EODHD")

    __table_args__ = (
        UniqueConstraint("security_id", "symbol", "valid_from", name="uix_symbol_history_identity"),
    )


class UniverseMembership(Base):
    """Point-in-time universe membership used to eliminate survivorship bias."""
    __tablename__ = "universe_membership"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    universe: Mapped[str] = mapped_column(String, index=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    effective_from: Mapped[dt_date] = mapped_column(Date, index=True)
    effective_to: Mapped[Optional[dt_date]] = mapped_column(Date, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    source: Mapped[str] = mapped_column(String, default="EODHD")
    source_run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("pipeline_runs.id"), index=True)

    __table_args__ = (
        UniqueConstraint(
            "universe",
            "ticker",
            "effective_from",
            "source",
            name="uix_universe_membership_period_source",
        ),
        Index(
            "ix_universe_membership_universe_interval",
            "universe",
            "effective_from",
            "effective_to",
            "ticker",
        ),
    )


class CorporateAction(Base):
    __tablename__ = "corporate_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    ex_date: Mapped[dt_date] = mapped_column(Date, index=True)
    action_type: Mapped[str] = mapped_column(String, index=True)
    split_factor: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    cash_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    currency: Mapped[Optional[str]] = mapped_column(String)
    available_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    source: Mapped[str] = mapped_column(String, default="EODHD")
    source_id: Mapped[Optional[str]] = mapped_column(String)

    __table_args__ = (
        UniqueConstraint("ticker", "ex_date", "action_type", "source_id", name="uix_corporate_action_source"),
    )


class RawDataSnapshot(Base):
    __tablename__ = "raw_data_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String, index=True)
    dataset: Mapped[str] = mapped_column(String, index=True)
    as_of_date: Mapped[Optional[dt_date]] = mapped_column(Date, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    checksum: Mapped[str] = mapped_column(String, unique=True, index=True)
    storage_path: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="stored", index=True)
    details: Mapped[Optional[Any]] = mapped_column(JSON)


class FundamentalVersion(Base):
    """A filing version with an explicit information-availability timestamp."""
    __tablename__ = "fundamental_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    period_end: Mapped[dt_date] = mapped_column(Date, index=True)
    period_type: Mapped[str] = mapped_column(String, index=True)
    filing_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    available_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    availability_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    income_statement: Mapped[Optional[Any]] = mapped_column(JSON)
    balance_sheet: Mapped[Optional[Any]] = mapped_column(JSON)
    cash_flow: Mapped[Optional[Any]] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String, default="EODHD")
    raw_snapshot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("raw_data_snapshots.id"))

    __table_args__ = (
        UniqueConstraint(
            "ticker", "period_end", "period_type", "available_at", "revision",
            name="uix_fundamental_point_in_time",
        ),
    )


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_name: Mapped[str] = mapped_column(String, index=True)
    target_date: Mapped[Optional[dt_date]] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String, default="running", index=True)
    stage: Mapped[Optional[str]] = mapped_column(String)
    version: Mapped[str] = mapped_column(String, default="v1")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    records_processed: Mapped[int] = mapped_column(Integer, default=0)
    quality_report: Mapped[Optional[Any]] = mapped_column(JSON)
    error_message: Mapped[Optional[str]] = mapped_column(Text)


class DataPublication(Base):
    __tablename__ = "data_publications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset: Mapped[str] = mapped_column(String, index=True)
    as_of_date: Mapped[dt_date] = mapped_column(Date, index=True)
    pipeline_run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"), index=True)
    status: Mapped[str] = mapped_column(String, default="published", index=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    __table_args__ = (
        UniqueConstraint("dataset", "as_of_date", name="uix_publication_dataset_date"),
    )


class FactorValue(Base):
    __tablename__ = "factor_values"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    as_of_date: Mapped[dt_date] = mapped_column(Date, index=True)
    factor_name: Mapped[str] = mapped_column(String, index=True)
    raw_value: Mapped[Optional[float]] = mapped_column(Float)
    normalized_value: Mapped[Optional[float]] = mapped_column(Float)
    version: Mapped[str] = mapped_column(String, index=True)
    available_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    source_run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("pipeline_runs.id"))
    details: Mapped[Optional[Any]] = mapped_column(JSON)

    __table_args__ = (
        UniqueConstraint(
            "ticker",
            "as_of_date",
            "factor_name",
            "version",
            "source_run_id",
            name="uix_factor_value_run",
        ),
    )


class PersonalWatchlistItem(Base):
    """Ordered watchlist for the single-user personal workspace."""

    __tablename__ = "personal_watchlist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String, unique=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        onupdate=utc_now,
    )


class PersonalWorkspaceState(Base):
    """Singleton state that distinguishes uninitialized from intentionally empty data."""

    __tablename__ = "personal_workspace_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    watchlist_initialized: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        onupdate=utc_now,
    )


class TickerValuationScenario(Base):
    """Saved transparent DCF inputs for one ticker and named scenario."""

    __tablename__ = "ticker_valuation_scenarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    scenario: Mapped[str] = mapped_column(String)
    fcf_growth_rate: Mapped[float] = mapped_column(Float)
    wacc: Mapped[float] = mapped_column(Float)
    perpetual_growth: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        onupdate=utc_now,
    )

    __table_args__ = (
        UniqueConstraint(
            "ticker",
            "scenario",
            name="uix_ticker_valuation_scenario",
        ),
    )


class DecisionBriefCache(Base):
    """Validated, evidence-bound AI decision brief cache."""

    __tablename__ = "decision_brief_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    evidence_hash: Mapped[str] = mapped_column(String, index=True)
    model: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    evidence_ids: Mapped[Any] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    __table_args__ = (
        UniqueConstraint(
            "ticker",
            "evidence_hash",
            "model",
            name="uix_decision_brief_evidence_model",
        ),
    )


class StrategyDefinition(Base):
    __tablename__ = "strategy_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, index=True)
    version: Mapped[str] = mapped_column(String)
    config: Mapped[Any] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    __table_args__ = (UniqueConstraint("name", "version", name="uix_strategy_version"),)


class SignalSnapshot(Base):
    __tablename__ = "signal_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategy_definitions.id"), index=True)
    backtest_run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("backtest_runs.id"), index=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    as_of_date: Mapped[dt_date] = mapped_column(Date, index=True)
    score: Mapped[float] = mapped_column(Float)
    rank: Mapped[int] = mapped_column(Integer)
    target_weight: Mapped[Optional[float]] = mapped_column(Float)
    factor_details: Mapped[Optional[Any]] = mapped_column(JSON)

    __table_args__ = (
        UniqueConstraint(
            "backtest_run_id",
            "ticker",
            "as_of_date",
            name="uix_backtest_signal_date",
        ),
    )


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[Optional[int]] = mapped_column(ForeignKey("strategy_definitions.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    start_date: Mapped[dt_date] = mapped_column(Date)
    end_date: Mapped[dt_date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String, default="running", index=True)
    config: Mapped[Any] = mapped_column(JSON)
    metrics: Mapped[Optional[Any]] = mapped_column(JSON)
    equity_curve: Mapped[Optional[Any]] = mapped_column(JSON)
    attribution: Mapped[Optional[Any]] = mapped_column(JSON)
    diagnostics: Mapped[Optional[Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
