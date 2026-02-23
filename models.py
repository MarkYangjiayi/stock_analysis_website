import os
from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Optional, Any, List

from sqlalchemy import (
    String,
    Integer,
    Numeric,
    DateTime,
    Date,
    BigInteger,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship



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
    last_updated: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, doc="基础信息最后同步时间")

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
    
    # 完整原始报表存入 JSONB
    income_statement: Mapped[Optional[Any]] = mapped_column(JSONB)
    balance_sheet: Mapped[Optional[Any]] = mapped_column(JSONB)
    cash_flow: Mapped[Optional[Any]] = mapped_column(JSONB)

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
    sector: Mapped[Optional[str]] = mapped_column(String, index=True)
    industry: Mapped[Optional[str]] = mapped_column(String, index=True)
    
    # 基本面指标
    market_cap: Mapped[Optional[Decimal]] = mapped_column(Numeric, index=True)
    pe_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    pb_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    dividend_yield: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    roe: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    debt_to_equity: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    fcf: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    gross_margin: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    sales_growth_5yr: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    
    # 技术面指标 (通过近60天K线运算)
    close: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    volume: Mapped[Optional[int]] = mapped_column(BigInteger)
    ma20: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    ma50: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    rsi_14: Mapped[Optional[Decimal]] = mapped_column(Numeric)

    # 联合唯一索引支持 UPSERT
    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uix_screener_snapshot_ticker_date"),
    )
