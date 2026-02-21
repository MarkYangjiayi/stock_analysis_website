import asyncio
import logging
from typing import Optional, Dict, Any

import pandas as pd
import pandas_ta_classic as ta
from sqlalchemy import select, asc, desc
from sqlalchemy.ext.asyncio import AsyncSession

from models import Ticker, DailyPrice, FinancialStatement

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------
# 定量分析与读取服务
# ------------------------------------------------------------------------

async def get_analyzed_stock_data(ticker: str, db: AsyncSession) -> Optional[Dict[str, Any]]:
    """
    根据架构文档的数据读取逻辑：
    - 读取指定 ticker 的基础 Profile
    - 联合读取最近 300 天的 Historical Data (Daily Prices)
    - 使用 pandas-ta 进行衍生指标计算
    - 序列化并返回
    """
    ticker = ticker.upper()
    logger.info(f"Analyzing data for {ticker}")

    # 1. 异步查询 Ticker 基础信息
    ticker_stmt = select(Ticker).where(Ticker.ticker == ticker)
    ticker_result = await db.execute(ticker_stmt)
    ticker_obj = ticker_result.scalar_one_or_none()
    
    if not ticker_obj:
        logger.warning(f"Ticker {ticker} not found in database.")
        return None

    # 构建 Profile 字典
    profile = {
        "ticker": ticker_obj.ticker,
        "name": ticker_obj.name,
        "exchange": ticker_obj.exchange,
        "sector": ticker_obj.sector,
        "industry": ticker_obj.industry,
        "description": ticker_obj.description,
        "currency": ticker_obj.currency,
        "last_updated": ticker_obj.last_updated.isoformat() if ticker_obj.last_updated else None,
    }

    # 2. 异步查询最近 300 天的历史 K 线
    # 需要对 date 进行降序取 300 条，然后再把这 300 条变回升序供 pandas 序列计算
    subq = select(DailyPrice).where(DailyPrice.ticker == ticker)\
        .order_by(DailyPrice.date.desc())\
        .limit(300)\
        .subquery()

    price_stmt = select(DailyPrice).join(subq, DailyPrice.id == subq.c.id).order_by(DailyPrice.date.asc())
    price_result = await db.execute(price_stmt)
    price_records = price_result.scalars().all()

    historical_data = []
    
    # 3. 如果有 K 线数据则借助 pandas-ta 计算技术面指标
    if price_records:
        # 转换为 DataFrame
        df = pd.DataFrame([{
            "date": rec.date,
            "open": float(rec.open) if rec.open else None,
            "high": float(rec.high) if rec.high else None,
            "low": float(rec.low) if rec.low else None,
            "close": float(rec.close) if rec.close else None,
            "volume": rec.volume
        } for rec in price_records])
        
        # 确保 date 排序升序
        df = df.sort_values(by='date').reset_index(drop=True)

        # 挂载计算各种技术指标
        # 移动平均线 MA20, MA50
        df['MA20'] = ta.sma(df['close'], length=20)
        df['MA50'] = ta.sma(df['close'], length=50)
        
        # 相对强弱指数 RSI (14)
        df['RSI'] = ta.rsi(df['close'], length=14)
        
        # MACD (12, 26, 9)
        # macd 返回 DataFrame 包含 MACD_12_26_9, MACDh_12_26_9(Histogram), MACDs_12_26_9(Signal)
        macd_df = ta.macd(df['close'], fast=12, slow=26, signal=9)
        if macd_df is not None and not macd_df.empty:
            df = pd.concat([df, macd_df], axis=1)
            # 为了 API 字段统一，重命名一下
            df.rename(columns={
                'MACD_12_26_9': 'MACD',
                'MACDs_12_26_9': 'MACD_Signal',
                'MACDh_12_26_9': 'MACD_Hist'
            }, inplace=True, errors='ignore')
        else:
            df['MACD'] = None
            df['MACD_Signal'] = None
            df['MACD_Hist'] = None

        # 清洗最终数据包以支持 JSON 序列化 (将 Pandas NaN/NaT 转为 None)
        df = df.replace({pd.NA: None, float('nan'): None})
        
        # DateTime objects to string for JSON serialization
        df['date'] = df['date'].astype(str)

        historical_data = df.to_dict(orient='records')

    # 4. 组装返回结果
    response_data = {
        "profile": profile,
        "historical_data": historical_data
    }
    
    return response_data

# ------------------------------------------------------------------------
# 基本面估值服务 (Fundamental Valuation)
# ------------------------------------------------------------------------

async def get_fundamental_valuation(ticker: str, db: AsyncSession) -> Optional[Dict[str, Any]]:
    """
    计算 TTM 核心指标与简易 DCF (现金流折现) 模型估值。
    """
    ticker = ticker.upper()
    
    # 优先尝试获取最近 4 个季度的报表
    stmt_q = select(FinancialStatement).where(
        FinancialStatement.ticker == ticker,
        FinancialStatement.period == "Quarterly"
    ).order_by(FinancialStatement.fiscal_date.desc()).limit(4)
    result_q = await db.execute(stmt_q)
    records = list(result_q.scalars().all())
    
    # 如果没有季度报表，则退而求其次获取最近 1 个年度报表
    if not records:
        stmt_y = select(FinancialStatement).where(
            FinancialStatement.ticker == ticker,
            FinancialStatement.period == "Yearly"
        ).order_by(FinancialStatement.fiscal_date.desc()).limit(1)
        result_y = await db.execute(stmt_y)
        records = list(result_y.scalars().all())
        
    if not records:
        logger.warning(f"No financial statements found for {ticker}")
        return None

    def _safe_float(val) -> float:
        try:
            return float(val) if val is not None else 0.0
        except (ValueError, TypeError):
            return 0.0

    ttm_revenue = 0.0
    ttm_net_income = 0.0
    ttm_fcf = 0.0
    
    # 遍历计算 TTM (Trailing Twelve Months) 累计值
    for rec in records:
        # 获取当年/季的数据
        inc_stmt = rec.income_statement or {}
        cf_stmt = rec.cash_flow or {}
        
        # 处理可能以字符串存在的数据
        ttm_revenue += _safe_float(inc_stmt.get('totalRevenue', rec.revenue))
        ttm_net_income += _safe_float(inc_stmt.get('netIncome', rec.net_income))
        ttm_fcf += _safe_float(cf_stmt.get('freeCashFlow', 0))

    # 取最近一期资产负债表(Point-in-time)
    latest_bs = records[0].balance_sheet or {}
    
    total_assets = _safe_float(latest_bs.get('totalAssets'))
    total_liab = _safe_float(latest_bs.get('totalLiab', latest_bs.get('totalLiabilities')))
    total_equity = _safe_float(latest_bs.get('totalStockholderEquity'))
    shares_out = _safe_float(latest_bs.get('commonStockSharesOutstanding'))
    cash_equiv = _safe_float(latest_bs.get('cashAndCashEquivalents'))
    total_debt = _safe_float(latest_bs.get('totalDebt'))
    
    # ROE 计算
    roe = (ttm_net_income / total_equity) if total_equity > 0 else 0.0

    # DCF 模型参数
    fcf_growth_rate = 0.10      # 10% 未来5年复合增长率
    wacc = 0.09                 # 9% 折现率
    perpetual_growth = 0.025    # 2.5% 永续增长率
    
    dcf_5yr_sum = 0.0
    
    # 计算前5年自由现金流折现
    if ttm_fcf > 0:
        for i in range(1, 6):
            proj_fcf = ttm_fcf * ((1 + fcf_growth_rate) ** i)
            pv_fcf = proj_fcf / ((1 + wacc) ** i)
            dcf_5yr_sum += pv_fcf
            
        # 计算终值折现 (Terminal Value)
        terminal_value = (ttm_fcf * ((1 + fcf_growth_rate) ** 5) * (1 + perpetual_growth)) / (wacc - perpetual_growth)
        pv_tv = terminal_value / ((1 + wacc) ** 5)
        
        # 企业价值 与 股权价值
        enterprise_value = dcf_5yr_sum + pv_tv
        equity_value = enterprise_value + cash_equiv - total_debt
    else:
        equity_value = 0.0
        
    intrinsic_value_per_share = (equity_value / shares_out) if shares_out > 0 else 0.0

    # 获取最新股价以计算安全边际
    price_stmt = select(DailyPrice).where(DailyPrice.ticker == ticker).order_by(DailyPrice.date.desc()).limit(1)
    price_result = await db.execute(price_stmt)
    latest_price_rec = price_result.scalar_one_or_none()
    
    current_price = _safe_float(latest_price_rec.close) if latest_price_rec else 0.0
    margin_of_safety = 0.0
    
    if intrinsic_value_per_share > 0 and current_price > 0:
        # 安全边际 = (内在价值 - 当前股价) / 内在价值
        margin_of_safety = (intrinsic_value_per_share - current_price) / intrinsic_value_per_share

    return {
        "ttm": {
            "revenue": ttm_revenue,
            "net_income": ttm_net_income,
            "free_cash_flow": ttm_fcf,
            "roe": roe
        },
        "balance_sheet_latest": {
            "total_assets": total_assets,
            "total_liabilities": total_liab,
            "total_stockholder_equity": total_equity,
            "shares_outstanding": shares_out
        },
        "valuation": {
            "dcf_intrinsic_value_per_share": intrinsic_value_per_share,
            "current_price": current_price,
            "margin_of_safety": margin_of_safety,
            "assumptions": {
                "fcf_growth_rate_5yr": fcf_growth_rate,
                "wacc": wacc,
                "perpetual_growth": perpetual_growth
            }
        }
    }
