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


class IndexValuationSnapshot(Base):
    """Immutable month-end index-level valuation for one published pipeline run.

    Aggregates reconstructed per-member multiples over point-in-time index
    membership. Months with insufficient coverage stay gaps: value columns are
    null and the run's quality report carries the reason, never zero-filled.
    """

    __tablename__ = "index_valuation_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"))
    universe: Mapped[str] = mapped_column(String)
    date: Mapped[dt_date] = mapped_column(Date)

    member_count: Mapped[int] = mapped_column(Integer)
    covered_count: Mapped[int] = mapped_column(Integer)
    loss_maker_count: Mapped[int] = mapped_column(Integer)

    coverage_pct: Mapped[Optional[float]] = mapped_column(Float)
    equity_total: Mapped[Optional[float]] = mapped_column(Float)
    earnings_ttm_total: Mapped[Optional[float]] = mapped_column(Float)
    earnings_ttm_earners: Mapped[Optional[float]] = mapped_column(Float)
    index_pe: Mapped[Optional[float]] = mapped_column(Float)
    index_pe_earners: Mapped[Optional[float]] = mapped_column(Float)
    median_pe: Mapped[Optional[float]] = mapped_column(Float)
    reason: Mapped[Optional[str]] = mapped_column(String)

    __table_args__ = (
        Index(
            "ix_index_valuation_snapshots_run_universe_date",
            "pipeline_run_id",
            "universe",
            "date",
            unique=True,
        ),
    )


class IndexValuationBackfillCheckpoint(Base):
    """Latest successfully normalized fundamentals fetch for one member.

    Raw snapshots are immutable and deduplicated by payload checksum, so their
    ``fetched_at`` value cannot represent a repeated fetch of identical data.
    This checkpoint is updated in the same transaction as normalization and is
    therefore safe to use for the paid re-fetch guard.
    """

    __tablename__ = "index_valuation_backfill_checkpoints"

    ticker: Mapped[str] = mapped_column(
        ForeignKey("tickers.ticker"), primary_key=True
    )
    fundamentals_normalized_at: Mapped[datetime] = mapped_column(
        DateTime
    )
    raw_snapshot_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("raw_data_snapshots.id")
    )

    __table_args__ = (
        Index(
            "ix_index_valuation_backfill_checkpoints_normalized_at",
            "fundamentals_normalized_at",
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
    # Canonical PEG is positive-only; retain the provider value for audit/debugging.
    peg_ratio_raw: Mapped[Optional[Decimal]] = mapped_column(Numeric)
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
    # Consensus revisions and short interest (services/expectation_signals.py)
    eps_revision_30d: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    eps_revision_90d: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    eps_revision_breadth_30d: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    eps_estimate_analysts: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    short_interest_change: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    short_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric)

    # 技术面指标 (通过近60天K线运算)
    technical_quality: Mapped[Optional[str]] = mapped_column(String)
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
    provider_beta: Mapped[Optional[Decimal]] = mapped_column(Numeric)
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


class DailyReportRun(Base):
    """Auditable final content and delivery state for one scheduled report."""

    __tablename__ = "daily_report_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_type: Mapped[str] = mapped_column(String, index=True)
    renderer_version: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, index=True)
    source_results: Mapped[Optional[Any]] = mapped_column(JSON)
    market_context: Mapped[Optional[Any]] = mapped_column(JSON)
    content: Mapped[str] = mapped_column(Text)
    notification_delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        index=True,
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class EarningsQualityAnalysisRun(Base):
    """Durable, evidence-bound analysis of one reported financial period."""

    __tablename__ = "earnings_quality_analysis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(ForeignKey("tickers.ticker"), index=True)
    period_end: Mapped[dt_date] = mapped_column(Date, index=True)
    period_type: Mapped[str] = mapped_column(String, index=True)
    statement_fingerprint: Mapped[str] = mapped_column(String, index=True)
    cache_identity: Mapped[str] = mapped_column(String, index=True)
    model: Mapped[str] = mapped_column(String)
    prompt_version: Mapped[str] = mapped_column(String)
    schema_version: Mapped[str] = mapped_column(String)

    status: Mapped[str] = mapped_column(String, default="queued", index=True)
    stage: Mapped[str] = mapped_column(String, default="queued")
    active_key: Mapped[Optional[str]] = mapped_column(String, unique=True)
    global_slot: Mapped[Optional[str]] = mapped_column(String, unique=True)
    owner_token: Mapped[Optional[str]] = mapped_column(String, index=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)

    sec_accession: Mapped[Optional[str]] = mapped_column(String, index=True)
    source_checksum: Mapped[Optional[str]] = mapped_column(String, index=True)
    source_snapshots: Mapped[Optional[Any]] = mapped_column(JSON)
    ai_result: Mapped[Optional[Any]] = mapped_column(JSON)
    result: Mapped[Optional[Any]] = mapped_column(JSON)
    validation_report: Mapped[Optional[Any]] = mapped_column(JSON)
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        onupdate=utc_now,
    )

    __table_args__ = (
        Index(
            "ix_earnings_quality_period_cache",
            "ticker",
            "period_end",
            "period_type",
            "cache_identity",
        ),
    )


class FinancialFlowRun(Base):
    """Durable deterministic financial-flow enrichment for one reported period."""

    __tablename__ = "financial_flow_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(ForeignKey("tickers.ticker"), index=True)
    period_end: Mapped[dt_date] = mapped_column(Date, index=True)
    period_type: Mapped[str] = mapped_column(String, index=True)
    input_fingerprint: Mapped[str] = mapped_column(String, index=True)
    cache_identity: Mapped[str] = mapped_column(String, index=True)
    schema_version: Mapped[str] = mapped_column(String)

    status: Mapped[str] = mapped_column(String, default="queued", index=True)
    stage: Mapped[str] = mapped_column(String, default="queued")
    coverage_level: Mapped[str] = mapped_column(String, default="consolidated", index=True)
    active_key: Mapped[Optional[str]] = mapped_column(String, unique=True)
    global_slot: Mapped[Optional[str]] = mapped_column(String, unique=True)
    owner_token: Mapped[Optional[str]] = mapped_column(String, index=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)

    source_snapshots: Mapped[Optional[Any]] = mapped_column(JSON)
    result: Mapped[Optional[Any]] = mapped_column(JSON)
    validation_report: Mapped[Optional[Any]] = mapped_column(JSON)
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        onupdate=utc_now,
    )

    __table_args__ = (
        UniqueConstraint(
            "ticker",
            "period_end",
            "period_type",
            "cache_identity",
            name="uix_financial_flow_period_cache",
        ),
    )


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


class ScreenerFundamentalCache(Base):
    """Latest normalized fundamentals used to build daily screener rows.

    The immutable raw payload remains in ``raw_data_snapshots``. This compact
    cache avoids reopening thousands of large raw files and lets failed daily
    publications resume without paying for the same provider data again.
    """

    __tablename__ = "screener_fundamental_cache"

    ticker: Mapped[str] = mapped_column(
        ForeignKey("tickers.ticker"), primary_key=True
    )
    as_of_date: Mapped[dt_date] = mapped_column(Date, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    normalized_data: Mapped[Any] = mapped_column(JSON)
    raw_snapshot_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("raw_data_snapshots.id")
    )
    source: Mapped[str] = mapped_column(String, default="EODHD")


class ScreenerFundamentalRefreshPlan(Base):
    """Stable paid-refresh cohort for one market date.

    Keeping the plan durable prevents a failed publication retry from moving
    on to a second paid cohort after the first cohort was cached successfully.
    """

    __tablename__ = "screener_fundamental_refresh_plans"

    target_date: Mapped[dt_date] = mapped_column(Date, primary_key=True)
    tickers: Mapped[Any] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


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


class RsiAlert(Base):
    """One successfully delivered daily RSI alert for a monitored ticker."""

    __tablename__ = "rsi_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    price_date: Mapped[dt_date] = mapped_column(Date, index=True)
    period: Mapped[int] = mapped_column(Integer, default=14)
    rsi_value: Mapped[float] = mapped_column(Float)
    zone: Mapped[str] = mapped_column(String)
    threshold: Mapped[float] = mapped_column(Float)
    notified_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    __table_args__ = (
        UniqueConstraint(
            "ticker",
            "price_date",
            "period",
            "zone",
            name="uix_rsi_alert_ticker_date_period_zone",
        ),
    )


class DataFreshnessAlert(Base):
    """One delivered stale-data alert for a dataset and the session it missed."""

    __tablename__ = "data_freshness_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset: Mapped[str] = mapped_column(String, index=True)
    expected_date: Mapped[dt_date] = mapped_column(Date, index=True)
    published_date: Mapped[Optional[dt_date]] = mapped_column(Date)
    notified_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    __table_args__ = (
        UniqueConstraint(
            "dataset",
            "expected_date",
            name="uix_data_freshness_alert_dataset_date",
        ),
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
    forecast_inputs: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
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


# ------------------------------------------------------------------------
# Trending themes (题材热度)
#
# The taxonomy lives in services/themes/taxonomy/*.yaml and is synced into
# `themes`. Membership rows are validity intervals (valid_to is exclusive).
# Daily outputs follow the factor publication pattern: rows carry the run
# that produced them and readers join DataPublication(dataset="theme_trends").
# ------------------------------------------------------------------------


class Theme(Base):
    """Curated theme definition synced from the Git-managed taxonomy file."""

    __tablename__ = "themes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String)
    name_en: Mapped[str] = mapped_column(String)
    name_zh: Mapped[str] = mapped_column(String)
    megatrend: Mapped[Optional[str]] = mapped_column(String)
    definition: Mapped[str] = mapped_column(Text)
    keywords: Mapped[Any] = mapped_column(JSON)
    negative_keywords: Mapped[Any] = mapped_column(JSON)
    proxy_etfs: Mapped[Any] = mapped_column(JSON)
    seed_core: Mapped[Any] = mapped_column(JSON)
    pins: Mapped[Any] = mapped_column(JSON)
    bans: Mapped[Any] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, default="active", index=True)
    taxonomy_version: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (UniqueConstraint("slug", name="uix_themes_slug"),)


class ThemeMember(Base):
    """One company's membership interval in a theme; valid_to is exclusive."""

    __tablename__ = "theme_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    theme_id: Mapped[int] = mapped_column(ForeignKey("themes.id"), index=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    tier: Mapped[str] = mapped_column(String)
    weight: Mapped[float] = mapped_column(Float)
    exposure: Mapped[int] = mapped_column(Integer)
    role: Mapped[Optional[str]] = mapped_column(String)
    sources: Mapped[Any] = mapped_column(JSON)
    evidence_quote: Mapped[Optional[str]] = mapped_column(Text)
    evidence_source: Mapped[Optional[str]] = mapped_column(String)
    evidence_as_of: Mapped[Optional[dt_date]] = mapped_column(Date)
    llm_model: Mapped[Optional[str]] = mapped_column(String)
    prompt_version: Mapped[Optional[str]] = mapped_column(String)
    review_status: Mapped[str] = mapped_column(String)
    valid_from: Mapped[dt_date] = mapped_column(Date, index=True)
    valid_to: Mapped[Optional[dt_date]] = mapped_column(Date, index=True)
    source_run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("pipeline_runs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    __table_args__ = (
        UniqueConstraint("theme_id", "ticker", "valid_from", name="uix_theme_members_theme_ticker_valid_from"),
        Index("ix_theme_members_theme_interval", "theme_id", "valid_from", "valid_to"),
    )


class ThemeMemberVerification(Base):
    """Cached LLM verification of one (theme, company, input text) candidate."""

    __tablename__ = "theme_member_verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    theme_id: Mapped[int] = mapped_column(ForeignKey("themes.id"), index=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    input_hash: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    prompt_version: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    exposure: Mapped[Optional[int]] = mapped_column(Integer)
    role: Mapped[Optional[str]] = mapped_column(String)
    evidence_quote: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    __table_args__ = (
        UniqueConstraint("theme_id", "ticker", "input_hash", name="uix_theme_member_verifications_key"),
    )


class EtfHoldingSnapshot(Base):
    """Holdings of a theme proxy ETF as observed on one fetch date."""

    __tablename__ = "etf_holdings_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    etf: Mapped[str] = mapped_column(String, index=True)
    as_of_date: Mapped[dt_date] = mapped_column(Date, index=True)
    source: Mapped[str] = mapped_column(String)
    holding_key: Mapped[str] = mapped_column(String)
    holding_ticker: Mapped[Optional[str]] = mapped_column(String, index=True)
    holding_name: Mapped[Optional[str]] = mapped_column(String)
    exchange: Mapped[Optional[str]] = mapped_column(String)
    weight_pct: Mapped[Optional[float]] = mapped_column(Float)
    inception_date: Mapped[Optional[dt_date]] = mapped_column(Date)
    raw_snapshot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("raw_data_snapshots.id"))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    __table_args__ = (
        UniqueConstraint("etf", "as_of_date", "source", "holding_key", name="uix_etf_holdings_etf_date_source_key"),
    )


class NewsIngestDay(Base):
    """Completion checkpoint for one UTC day of the market-wide news feed."""

    __tablename__ = "news_ingest_days"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[dt_date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String, index=True)
    pages: Mapped[int] = mapped_column(Integer, default=0)
    articles_total: Mapped[int] = mapped_column(Integer, default=0)
    articles_us: Mapped[int] = mapped_column(Integer, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_snapshot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("raw_data_snapshots.id"))
    source_run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("pipeline_runs.id"))
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    __table_args__ = (UniqueConstraint("day", name="uix_news_ingest_days_day"),)


class NewsItem(Base):
    """Deduplicated market-news headline kept for evidence (short retention)."""

    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url_hash: Mapped[str] = mapped_column(String)
    day: Mapped[dt_date] = mapped_column(Date, index=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    title: Mapped[str] = mapped_column(Text)
    link: Mapped[Optional[str]] = mapped_column(String)
    source_domain: Mapped[Optional[str]] = mapped_column(String)
    tickers: Mapped[Any] = mapped_column(JSON)
    symbol_count: Mapped[int] = mapped_column(Integer, default=0)
    tags: Mapped[Optional[Any]] = mapped_column(JSON)
    raw_snapshot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("raw_data_snapshots.id"))
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    __table_args__ = (UniqueConstraint("url_hash", name="uix_news_items_url_hash"),)


class TickerAttentionDaily(Base):
    """Per-ticker article counts for one UTC news day (rows only where > 0)."""

    __tablename__ = "ticker_attention_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    day: Mapped[dt_date] = mapped_column(Date, index=True)
    article_count: Mapped[int] = mapped_column(Integer)
    focused_count: Mapped[int] = mapped_column(Integer, default=0)
    source_run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("pipeline_runs.id"))

    __table_args__ = (UniqueConstraint("ticker", "day", name="uix_ticker_attention_daily_ticker_day"),)


class ThemeDaily(Base):
    """Theme heat, components and lifecycle stage for one session and run."""

    __tablename__ = "theme_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"))
    theme_id: Mapped[int] = mapped_column(ForeignKey("themes.id"))
    date: Mapped[dt_date] = mapped_column(Date, index=True)
    scoring_version: Mapped[str] = mapped_column(String)
    pit: Mapped[bool] = mapped_column(Boolean, default=False)
    rankable: Mapped[bool] = mapped_column(Boolean, default=False)
    n_members: Mapped[int] = mapped_column(Integer, default=0)
    n_priced: Mapped[int] = mapped_column(Integer, default=0)
    ret_1d: Mapped[Optional[float]] = mapped_column(Float)
    basket_index: Mapped[Optional[float]] = mapped_column(Float)
    x5: Mapped[Optional[float]] = mapped_column(Float)
    x21: Mapped[Optional[float]] = mapped_column(Float)
    x63: Mapped[Optional[float]] = mapped_column(Float)
    x126: Mapped[Optional[float]] = mapped_column(Float)
    b50: Mapped[Optional[float]] = mapped_column(Float)
    nh: Mapped[Optional[float]] = mapped_column(Float)
    d_b50: Mapped[Optional[float]] = mapped_column(Float)
    ign: Mapped[int] = mapped_column(Integer, default=0)
    ign5: Mapped[int] = mapped_column(Integer, default=0)
    rvol5_median: Mapped[Optional[float]] = mapped_column(Float)
    att: Mapped[Optional[float]] = mapped_column(Float)
    att_breadth: Mapped[Optional[float]] = mapped_column(Float)
    score_price: Mapped[Optional[float]] = mapped_column(Float)
    score_breadth: Mapped[Optional[float]] = mapped_column(Float)
    score_volume: Mapped[Optional[float]] = mapped_column(Float)
    score_attention: Mapped[Optional[float]] = mapped_column(Float)
    h_raw: Mapped[Optional[float]] = mapped_column(Float)
    heat: Mapped[Optional[float]] = mapped_column(Float)
    heat_now: Mapped[Optional[float]] = mapped_column(Float)
    heat_trend: Mapped[Optional[float]] = mapped_column(Float)
    pct_now: Mapped[Optional[float]] = mapped_column(Float)
    pct_trend: Mapped[Optional[float]] = mapped_column(Float)
    rank_now: Mapped[Optional[int]] = mapped_column(Integer)
    rank_trend: Mapped[Optional[int]] = mapped_column(Integer)
    rank_change: Mapped[Optional[int]] = mapped_column(Integer)
    streak_top5: Mapped[int] = mapped_column(Integer, default=0)
    ts_z: Mapped[Optional[float]] = mapped_column(Float)
    cocorr: Mapped[Optional[float]] = mapped_column(Float)
    cocorr_pct: Mapped[Optional[float]] = mapped_column(Float)
    emerge: Mapped[bool] = mapped_column(Boolean, default=False)
    attention_only: Mapped[bool] = mapped_column(Boolean, default=False)
    attention_degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    candidate_stage: Mapped[str] = mapped_column(String)
    stage: Mapped[str] = mapped_column(String, index=True)
    stage_prev: Mapped[Optional[str]] = mapped_column(String)
    stage_changed: Mapped[bool] = mapped_column(Boolean, default=False)
    emerging_first_20: Mapped[bool] = mapped_column(Boolean, default=False)
    proxy_etf: Mapped[Optional[str]] = mapped_column(String)
    proxy_x21: Mapped[Optional[float]] = mapped_column(Float)
    leaders: Mapped[Optional[Any]] = mapped_column(JSON)
    flags: Mapped[Optional[Any]] = mapped_column(JSON)

    __table_args__ = (
        Index("ix_theme_daily_run_theme_date", "source_run_id", "theme_id", "date", unique=True),
        Index("ix_theme_daily_theme_date", "theme_id", "date"),
    )


class ThemeMemberDaily(Base):
    """Per-member metrics for the as-of session of one theme_trends run."""

    __tablename__ = "theme_member_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"))
    theme_id: Mapped[int] = mapped_column(ForeignKey("themes.id"))
    ticker: Mapped[str] = mapped_column(String, index=True)
    date: Mapped[dt_date] = mapped_column(Date, index=True)
    tier: Mapped[str] = mapped_column(String)
    weight: Mapped[float] = mapped_column(Float)
    close: Mapped[Optional[float]] = mapped_column(Float)
    r1: Mapped[Optional[float]] = mapped_column(Float)
    x5: Mapped[Optional[float]] = mapped_column(Float)
    x21: Mapped[Optional[float]] = mapped_column(Float)
    pth: Mapped[Optional[float]] = mapped_column(Float)
    rvol5: Mapped[Optional[float]] = mapped_column(Float)
    att_z: Mapped[Optional[float]] = mapped_column(Float)
    ignited: Mapped[bool] = mapped_column(Boolean, default=False)
    first_ignition_date: Mapped[Optional[dt_date]] = mapped_column(Date)
    leader_score: Mapped[Optional[float]] = mapped_column(Float)
    leader_rank: Mapped[Optional[int]] = mapped_column(Integer)
    is_leader: Mapped[bool] = mapped_column(Boolean, default=False)
    sparkline: Mapped[Optional[Any]] = mapped_column(JSON)

    __table_args__ = (
        Index("ix_theme_member_daily_run_theme_ticker_date", "source_run_id", "theme_id", "ticker", "date", unique=True),
    )


class CompanyTrendDaily(Base):
    """Top trending companies (universe-wide) for the as-of session of one run."""

    __tablename__ = "company_trend_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"))
    ticker: Mapped[str] = mapped_column(String, index=True)
    date: Mapped[dt_date] = mapped_column(Date, index=True)
    rank: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    name: Mapped[Optional[str]] = mapped_column(String)
    sector: Mapped[Optional[str]] = mapped_column(String)
    industry: Mapped[Optional[str]] = mapped_column(String)
    close: Mapped[Optional[float]] = mapped_column(Float)
    market_cap: Mapped[Optional[float]] = mapped_column(Float)
    r1: Mapped[Optional[float]] = mapped_column(Float)
    x5: Mapped[Optional[float]] = mapped_column(Float)
    x5_sector: Mapped[Optional[float]] = mapped_column(Float)
    rvol5: Mapped[Optional[float]] = mapped_column(Float)
    att_z: Mapped[Optional[float]] = mapped_column(Float)
    pth: Mapped[Optional[float]] = mapped_column(Float)
    theme_slugs: Mapped[Optional[Any]] = mapped_column(JSON)
    flags: Mapped[Optional[Any]] = mapped_column(JSON)

    __table_args__ = (
        Index("ix_company_trend_daily_run_ticker_date", "source_run_id", "ticker", "date", unique=True),
    )


class ThemeBrief(Base):
    """Validated, evidence-cited theme brief; only passing output is cached."""

    __tablename__ = "theme_briefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    theme_id: Mapped[int] = mapped_column(ForeignKey("themes.id"), index=True)
    date: Mapped[dt_date] = mapped_column(Date, index=True)
    evidence_hash: Mapped[str] = mapped_column(String, index=True)
    model: Mapped[str] = mapped_column(String)
    prompt_version: Mapped[str] = mapped_column(String)
    evidence: Mapped[Any] = mapped_column(JSON)
    content: Mapped[str] = mapped_column(Text)
    validation_report: Mapped[Optional[Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    __table_args__ = (
        UniqueConstraint("theme_id", "evidence_hash", "model", name="uix_theme_briefs_evidence_model"),
    )


class ThemeAlert(Base):
    """One successfully delivered theme lifecycle alert (data date keyed)."""

    __tablename__ = "theme_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    theme_id: Mapped[int] = mapped_column(ForeignKey("themes.id"), index=True)
    date: Mapped[dt_date] = mapped_column(Date, index=True)
    alert_type: Mapped[str] = mapped_column(String)
    payload: Mapped[Optional[Any]] = mapped_column(JSON)
    notified_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    __table_args__ = (
        UniqueConstraint("theme_id", "alert_type", "date", name="uix_theme_alerts_theme_type_date"),
    )


class ThemeProposal(Base):
    """Owner-reviewed candidate for a new theme discovered from unmapped movers."""

    __tablename__ = "theme_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposal_key: Mapped[str] = mapped_column(String)
    label_en: Mapped[Optional[str]] = mapped_column(String)
    label_zh: Mapped[Optional[str]] = mapped_column(String)
    slug_suggestion: Mapped[Optional[str]] = mapped_column(String)
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    tickers: Mapped[Any] = mapped_column(JSON)
    evidence: Mapped[Optional[Any]] = mapped_column(JSON)
    first_seen: Mapped[dt_date] = mapped_column(Date)
    last_seen: Mapped[dt_date] = mapped_column(Date, index=True)
    hit_count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    llm_model: Mapped[Optional[str]] = mapped_column(String)
    prompt_version: Mapped[Optional[str]] = mapped_column(String)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (UniqueConstraint("proposal_key", name="uix_theme_proposals_key"),)
