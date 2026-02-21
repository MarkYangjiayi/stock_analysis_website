import asyncio
import logging
from typing import Optional, Dict, Any

import pandas as pd
import pandas_ta_classic as ta
from sqlalchemy import select, asc
from sqlalchemy.ext.asyncio import AsyncSession

from models import Ticker, DailyPrice

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
