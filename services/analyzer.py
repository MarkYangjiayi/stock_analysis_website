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

    # 4. 异步查询历史财报以提供前端图表数据 (过去20个季度或过去5年)
    stmt_q = select(FinancialStatement).where(
        FinancialStatement.ticker == ticker,
        FinancialStatement.period == "Quarterly"
    ).order_by(FinancialStatement.fiscal_date.desc()).limit(20)
    result_q = await db.execute(stmt_q)
    fs_records = list(result_q.scalars().all())

    if not fs_records:
        stmt_y = select(FinancialStatement).where(
            FinancialStatement.ticker == ticker,
            FinancialStatement.period == "Yearly"
        ).order_by(FinancialStatement.fiscal_date.desc()).limit(5)
        result_y = await db.execute(stmt_y)
        fs_records = list(result_y.scalars().all())

    historical_financials = []
    fs_records.reverse()  # 翻转为升序排列 (最老的数据在前)
    
    def _safe_float(val) -> float:
        try:
            return float(val) if val is not None else 0.0
        except (ValueError, TypeError):
            return 0.0

    for rec in fs_records:
        inc_stmt = rec.income_statement or {}
        rev = _safe_float(inc_stmt.get('totalRevenue', rec.revenue))
        ni = _safe_float(inc_stmt.get('netIncome', rec.net_income))
        gp = _safe_float(inc_stmt.get('grossProfit', 0.0))
        
        gross_margin = (gp / rev) if rev > 0 else 0.0
        
        historical_financials.append({
            "date": rec.fiscal_date.isoformat() if hasattr(rec.fiscal_date, 'isoformat') else str(rec.fiscal_date),
            "revenue": rev,
            "net_income": ni,
            "gross_margin": gross_margin
        })

    # 5. 组装返回结果
    response_data = {
        "profile": profile,
        "historical_data": historical_data,
        "historical_financials": historical_financials
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

    # --- 因子打分逻辑 (Factor Scoring 0-100) ---
    def clamp_score(score: float) -> int:
        return int(max(0, min(100, score)))

    # 1. Value 分数 (依据 PE = Price / (TTM Net Income / Shares Out) 计算)
    value_score = 50
    if shares_out > 0 and ttm_net_income > 0 and current_price > 0:
        eps = ttm_net_income / shares_out
        pe = current_price / eps
        if pe <= 0:
            value_score = 10  # 亏损企业给低分
        elif pe < 15:
            value_score = 90
        elif pe < 25:
            value_score = 70
        elif pe < 50:
            value_score = 50
        else:
            value_score = 30
    elif ttm_net_income <= 0:
        value_score = 20
        
    # 2. Quality 分数 (依据 ROE 映射)
    quality_score = 50
    if total_equity > 0:
        if roe > 0.20:
            quality_score = 90
        elif roe > 0.15:
            quality_score = 75
        elif roe > 0.10:
            quality_score = 60
        elif roe > 0.05:
            quality_score = 40
        else:
            quality_score = 20

    # 3. Growth 分数 (由于 TTM 循环顺序为近期在前，提取记录 [0] 和 [1] 或 [4] 测算增长率)
    growth_score = 50
    if len(records) > 1:
        # 简化处理：由于可能是季度数据或年度，取第 0 份与其之前的做对比
        # 如果是季度，1 个季度前可能受季节性影响，更为准确的做法是同比 (Yoy)，这里做简化的环比映射或近邻对比
        curr_rev = _safe_float(records[0].income_statement.get('totalRevenue', records[0].revenue) if records[0].income_statement else records[0].revenue)
        prev_rev = _safe_float(records[1].income_statement.get('totalRevenue', records[1].revenue) if records[1].income_statement else records[1].revenue)
        
        if prev_rev > 0:
            growth_rate = (curr_rev - prev_rev) / prev_rev
            if growth_rate > 0.20:
                growth_score = 90
            elif growth_rate > 0.10:
                growth_score = 75
            elif growth_rate > 0:
                growth_score = 60
            elif growth_rate > -0.10:
                growth_score = 40
            else:
                growth_score = 20
                
    # 4. Health 分数 (依据资产负债率 Total Liabilities / Total Assets 计算)
    health_score = 50
    if total_assets > 0:
        debt_ratio = total_liab / total_assets
        if debt_ratio < 0.3:
            health_score = 90
        elif debt_ratio < 0.5:
            health_score = 75
        elif debt_ratio < 0.7:
            health_score = 50
        elif debt_ratio < 0.9:
            health_score = 30
        else:
            health_score = 10
            
    # 5. Momentum 分数 (查询最近的 MA 和 RSI 进行打分)
    momentum_score = 50
    if latest_price_rec:
        try:
            # Re-calculating moving averages precisely for the latest date would typically be cached,
            # but we can fetch the last 60 days of prices to get MA50 and MA20 quickly for this factor.
            mo_stmt = select(DailyPrice).where(DailyPrice.ticker == ticker).order_by(DailyPrice.date.desc()).limit(60)
            mo_res = await db.execute(mo_stmt)
            mo_recs = list(mo_res.scalars().all())
            
            if len(mo_recs) >= 50:
                closes = [float(r.close) for r in mo_recs if r.close is not None]
                closes.reverse() # 变成升序，最近一天在最后
                import pandas as pd
                import pandas_ta_classic as ta
                
                s_closes = pd.Series(closes)
                ma20 = ta.sma(s_closes, length=20).iloc[-1]
                ma50 = ta.sma(s_closes, length=50).iloc[-1]
                rsi = ta.rsi(s_closes, length=14).iloc[-1]
                
                m_score = 50
                # MA 趋势加分
                if not pd.isna(ma20) and not pd.isna(ma50):
                    if current_price > ma20: m_score += 15
                    if current_price > ma50: m_score += 15
                    if ma20 > ma50: m_score += 10
                
                # RSI 状态
                if not pd.isna(rsi):
                    if 40 <= rsi <= 70:
                        m_score += 10
                    elif rsi > 70:  # 超买，可能面临回调
                        m_score -= 10
                    elif rsi < 30:  # 超卖，可能有反弹动能
                        m_score += 10
                        
                momentum_score = clamp_score(m_score)
        except Exception as e:
            logger.warning(f"Momentum scoring failed for {ticker}: {e}")
            momentum_score = 50

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
        },
        "factor_scores": {
            "value": value_score,
            "quality": quality_score,
            "growth": growth_score,
            "health": health_score,
            "momentum": momentum_score
        }
    }


async def batch_get_factor_scores(tickers: list[str], db: AsyncSession) -> list[Dict[str, Any]]:
    """
    Concurrently evaluate and gather factor scores for a list of tickers.
    Gracefully handles missing data by returning default zero scores.
    """
    async def fetch_score(ticker: str):
        try:
            val = await get_fundamental_valuation(ticker, db)
            if val and "factor_scores" in val:
                return {"ticker": ticker.upper(), "factor_scores": val["factor_scores"]}
        except Exception as e:
            logger.error(f"Error fetching factor scores for {ticker}: {e}")
            
        return {
            "ticker": ticker.upper(), 
            "factor_scores": {
                "value": 0, "quality": 0, "growth": 0, "health": 0, "momentum": 0
            }
        }

    tasks = [fetch_score(ticker) for ticker in tickers]
    results = await asyncio.gather(*tasks)
    return list(results)
