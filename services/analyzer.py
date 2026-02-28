import asyncio
import logging
from typing import Optional, Dict, Any

import pandas as pd
import pandas_ta_classic as ta
from sqlalchemy import select, asc, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from models import Ticker, DailyPrice, FinancialStatement, StockScreenerSnapshot

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------
# 定量分析与读取服务
# ------------------------------------------------------------------------

async def get_analyzed_stock_data(ticker: str, db: AsyncSession, interval: str = "1d") -> Optional[Dict[str, Any]]:
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

    # 2. 异步查询所有历史 K 线，不再局限于最近 300 天
    price_stmt = select(DailyPrice).where(DailyPrice.ticker == ticker).order_by(DailyPrice.date.asc())
    price_result = await db.execute(price_stmt)
    price_records = price_result.scalars().all()

    historical_data = []
    
    # 3. 如果有 K 线数据则借助 pandas-ta 计算技术面指标
    if price_records:
        # 转换为 DataFrame 连带包含 raw close 和 adjusted_close
        df = pd.DataFrame([{
            "date": rec.date,
            "open": float(rec.open) if rec.open else None,
            "high": float(rec.high) if rec.high else None,
            "low": float(rec.low) if rec.low else None,
            "close": float(rec.close) if rec.close else None,
            "adjusted_close": float(rec.adjusted_close) if rec.adjusted_close else None,
            "volume": float(rec.volume) if rec.volume is not None else 0.0
        } for rec in price_records])

        # 在应用前复权前，剔除完全没有收盘价的无效/停牌数据行 (防止后续为 None 导致前端挂掉)
        df.dropna(subset=['close'], inplace=True)

        # 应用前复权逻辑 (Backward Adjustment for Splits/Dividends)
        df['adj_factor'] = df.apply(
            lambda r: (r['adjusted_close'] / r['close']) if pd.notnull(r['close']) and r['close'] > 0 and pd.notnull(r['adjusted_close']) else 1.0,
            axis=1
        )

        df['open'] = df['open'] * df['adj_factor']
        df['high'] = df['high'] * df['adj_factor']
        df['low'] = df['low'] * df['adj_factor']
        df['close'] = df['adjusted_close']  # or df['close'] * df['adj_factor']
        df['volume'] = df['volume'] / df['adj_factor']

        # 丢弃中间列
        df.drop(columns=['adjusted_close', 'adj_factor'], inplace=True)
        
        # 确保 date 排序升序并转换为 Datetime 支持重采样
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values(by='date').reset_index(drop=True)

        # 依据 interval 进行 K 线重采样 (Resampling)
        if interval == '1wk':
            df.set_index('date', inplace=True)
            df = df.resample('W-FRI').agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            }).dropna(subset=['close']).reset_index()
        elif interval == '1mo':
            df.set_index('date', inplace=True)
            try:
                # pandas >= 2.2 使用 'ME' 
                df = df.resample('ME').agg({
                    'open': 'first',
                    'high': 'max',
                    'low': 'min',
                    'close': 'last',
                    'volume': 'sum'
                })
            except ValueError:
                df = df.resample('M').agg({
                    'open': 'first',
                    'high': 'max',
                    'low': 'min',
                    'close': 'last',
                    'volume': 'sum'
                })
            df = df.dropna(subset=['close']).reset_index()

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
        if pd.api.types.is_datetime64_any_dtype(df['date']):
            df['date'] = df['date'].dt.strftime('%Y-%m-%d')
        else:
            df['date'] = df['date'].astype(str)

        historical_data = df.to_dict(orient='records')

    # 4. 异步查询所有历史财报以提供前端图表数据
    stmt_q = select(FinancialStatement).where(
        FinancialStatement.ticker == ticker,
        FinancialStatement.period == "Quarterly"
    ).order_by(FinancialStatement.fiscal_date.desc())
    result_q = await db.execute(stmt_q)
    fs_records = list(result_q.scalars().all())

    if not fs_records:
        stmt_y = select(FinancialStatement).where(
            FinancialStatement.ticker == ticker,
            FinancialStatement.period == "Yearly"
        ).order_by(FinancialStatement.fiscal_date.desc())
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

async def filter_screener_stocks(request_data: dict, db: AsyncSession) -> dict:
    """
    Dynamically filters the `StockScreenerSnapshot` table based on a variety of parameters.
    Supports min/max conditions, exact match for sector/industry, sorting, and pagination.
    """
    stmt = select(StockScreenerSnapshot)
    count_stmt = select(func.count(StockScreenerSnapshot.id))
    
    conditions = []
    
    if request_data.get("market_cap_min") is not None:
        conditions.append(StockScreenerSnapshot.market_cap >= request_data["market_cap_min"])
    if request_data.get("market_cap_max") is not None:
        conditions.append(StockScreenerSnapshot.market_cap <= request_data["market_cap_max"])
        
    if request_data.get("pe_min") is not None:
        conditions.append(StockScreenerSnapshot.pe_ratio >= request_data["pe_min"])
    if request_data.get("pe_max") is not None:
        conditions.append(StockScreenerSnapshot.pe_ratio <= request_data["pe_max"])
        
    if request_data.get("pb_min") is not None:
        conditions.append(StockScreenerSnapshot.pb_ratio >= request_data["pb_min"])
    if request_data.get("pb_max") is not None:
        conditions.append(StockScreenerSnapshot.pb_ratio <= request_data["pb_max"])
        
    if request_data.get("sector") is not None and request_data.get("sector"):
        conditions.append(StockScreenerSnapshot.sector == request_data["sector"])
    if request_data.get("industry") is not None and request_data.get("industry"):
        conditions.append(StockScreenerSnapshot.industry == request_data["industry"])
        
    if request_data.get("rsi_14_min") is not None:
        conditions.append(StockScreenerSnapshot.rsi_14 >= request_data["rsi_14_min"])
    if request_data.get("rsi_14_max") is not None:
        conditions.append(StockScreenerSnapshot.rsi_14 <= request_data["rsi_14_max"])
        
    if request_data.get("volume_min") is not None:
        conditions.append(StockScreenerSnapshot.volume >= request_data["volume_min"])
        
    if request_data.get("price_min") is not None:
        conditions.append(StockScreenerSnapshot.close >= request_data["price_min"])
    if request_data.get("price_max") is not None:
        conditions.append(StockScreenerSnapshot.close <= request_data["price_max"])
        
    if request_data.get("dividend_yield_min") is not None:
        conditions.append(StockScreenerSnapshot.dividend_yield >= request_data["dividend_yield_min"])
        
    if request_data.get("price_above_ma50"):
        conditions.append(StockScreenerSnapshot.close > StockScreenerSnapshot.ma50)
        
    if request_data.get("price_below_ma50"):
        conditions.append(StockScreenerSnapshot.close < StockScreenerSnapshot.ma50)
        
    if request_data.get("roe_min") is not None:
        conditions.append(StockScreenerSnapshot.roe >= request_data["roe_min"])
        
    if request_data.get("debt_to_equity_max") is not None:
        conditions.append(StockScreenerSnapshot.debt_to_equity <= request_data["debt_to_equity_max"])
        
    if request_data.get("fcf_min") is not None:
        conditions.append(StockScreenerSnapshot.fcf >= request_data["fcf_min"])
        
    if request_data.get("gross_margin_min") is not None:
        conditions.append(StockScreenerSnapshot.gross_margin >= request_data["gross_margin_min"])
        
    if request_data.get("sales_growth_5yr_min") is not None:
        conditions.append(StockScreenerSnapshot.sales_growth_5yr >= request_data["sales_growth_5yr_min"])

    if conditions:
        stmt = stmt.where(*conditions)
        count_stmt = count_stmt.where(*conditions)
        
    # getting total count
    total_count_res = await db.execute(count_stmt)
    total_count = total_count_res.scalar_one_or_none() or 0
    
    # sorting
    sort_col_name = request_data.get("sort_by", "market_cap")
    # prevent injection or arbitrary column names by checking if column exists
    if hasattr(StockScreenerSnapshot, sort_col_name):
        sort_column = getattr(StockScreenerSnapshot, sort_col_name)
    else:
        sort_column = StockScreenerSnapshot.market_cap
        
    if request_data.get("sort_desc", True):
        stmt = stmt.order_by(desc(sort_column).nulls_last())
    else:
        stmt = stmt.order_by(asc(sort_column).nulls_last())
        
    # pagination
    limit = request_data.get("limit", 50)
    offset = request_data.get("offset", 0)
    
    stmt = stmt.limit(limit).offset(offset)
    
    # execute
    result = await db.execute(stmt)
    records = result.scalars().all()
    
    # Pre-fetch on-the-fly fundamental valuations for any records missing market_cap or pe_ratio
    async def fetch_valuation_for_missing(ticker: str):
        try:
            val = await get_fundamental_valuation(ticker, db)
            return ticker, val
        except Exception:
            return ticker, None

    tickers_to_fetch = [r.ticker for r in records if r.market_cap is None or r.pe_ratio is None]
    valuation_map = {}
    if tickers_to_fetch:
        val_tasks = [fetch_valuation_for_missing(t) for t in tickers_to_fetch]
        val_results = await asyncio.gather(*val_tasks)
        for t, val in val_results:
            valuation_map[t] = val

    items = []
    for r in records:
        market_cap = float(r.market_cap) if r.market_cap is not None else None
        pe_ratio = float(r.pe_ratio) if r.pe_ratio is not None else None
        
        # Hydrate fundamentals dynamically if missing and we have local financial statement data
        if (market_cap is None or pe_ratio is None) and r.ticker in valuation_map:
            val = valuation_map[r.ticker]
            if val:
                shares_out = val.get('balance_sheet_latest', {}).get('shares_outstanding', 0)
                ttm_net_income = val.get('ttm', {}).get('net_income', 0)
                current_price = float(r.close) if r.close is not None else 0
                
                if market_cap is None and shares_out > 0 and current_price > 0:
                    market_cap = shares_out * current_price
                    
                if pe_ratio is None and shares_out > 0 and ttm_net_income > 0 and current_price > 0:
                    eps = ttm_net_income / shares_out
                    if eps > 0:
                        pe_ratio = current_price / eps
                    else:
                        pe_ratio = -1 # Indicate negative P/E
        
        items.append({
            "ticker": r.ticker,
            "name": r.name,
            "sector": r.sector,
            "industry": r.industry,
            "market_cap": market_cap,
            "pe_ratio": pe_ratio,
            "pb_ratio": float(r.pb_ratio) if r.pb_ratio is not None else None,
            "dividend_yield": float(r.dividend_yield) if r.dividend_yield is not None else None,
            "roe": float(r.roe) if r.roe is not None else None,
            "debt_to_equity": float(r.debt_to_equity) if r.debt_to_equity is not None else None,
            "fcf": float(r.fcf) if r.fcf is not None else None,
            "gross_margin": float(r.gross_margin) if r.gross_margin is not None else None,
            "sales_growth_5yr": float(r.sales_growth_5yr) if r.sales_growth_5yr is not None else None,
            "close": float(r.close) if r.close is not None else None,
            "volume": r.volume,
            "ma20": float(r.ma20) if r.ma20 is not None else None,
            "ma50": float(r.ma50) if r.ma50 is not None else None,
            "rsi_14": float(r.rsi_14) if r.rsi_14 is not None else None,
            "date": str(r.date)
        })
        
    return {
        "total": total_count,
        "items": items,
        "limit": limit,
        "offset": offset
    }

def calculate_rrg(ticker_df: pd.DataFrame, benchmark_df: pd.DataFrame, window: int = 14) -> list[dict]:
    """
    计算相对轮动图 (RRG) 的核心指标: RS-Ratio 和 RS-Momentum。
    """
    if ticker_df.empty or benchmark_df.empty:
        return []

    # 1. 根据 'date' 进行 inner merge 对齐数据
    df = pd.merge(
        ticker_df[['date', 'close']], 
        benchmark_df[['date', 'close']], 
        on='date', 
        how='inner', 
        suffixes=('_ticker', '_bench')
    )
    
    logger.info(f"RRG DEBUG: After inner merge (date alignment), remaining rows: {len(df)}")
    
    if df.empty:
        return []
        
    # 2. 计算 Relative Strength (RS) 并进行初次 EMA 平滑过滤噪音
    df['rs'] = df['close_ticker'] / df['close_bench']
    smoothed_rs = df['rs'].ewm(span=14, adjust=False).mean()
    
    # 3. 计算 RS-Ratio (X轴)
    rs_rolling = smoothed_rs.rolling(window=window)
    rs_sma = rs_rolling.mean()
    rs_std = rs_rolling.std()
    
    rs_ratio_raw = 100 + ((smoothed_rs - rs_sma) / rs_std) * 10
    # 二次平滑：对生成的 Ratio 进行轻度 EMA 平滑，防止 X 轴剧烈跳变
    df['rs_ratio'] = rs_ratio_raw.ewm(span=5, adjust=False).mean()
    
    # 4. 计算 RS-Momentum (Y轴)
    ratio_rolling = df['rs_ratio'].rolling(window=window)
    ratio_sma = ratio_rolling.mean()
    ratio_std = ratio_rolling.std()
    
    rs_momentum_raw = 100 + ((df['rs_ratio'] - ratio_sma) / ratio_std) * 10
    # 二次平滑：对生成的 Momentum 也进行轻度 EMA 平滑
    df['rs_momentum'] = rs_momentum_raw.ewm(span=5, adjust=False).mean()
    
    # 5. 剔除 NaN 值，不再截取 tail_length，返回计算出的所有有效历史日期
    df_cleaned = df.dropna(subset=['rs_ratio', 'rs_momentum'])
    logger.info(f"RRG DEBUG: After computing SMA/StdDev and dropna(), remaining valid rows: {len(df_cleaned)}")
    
    df = df_cleaned.copy()
    
    # 针对 date 进行字符串转换
    df['date'] = df['date'].astype(str)
    
    # 6. 返回规范的列表字典格式
    return df[['date', 'rs_ratio', 'rs_momentum']].to_dict(orient='records')

async def get_rrg_data_for_tickers(
    tickers: list[str],
    db_session: AsyncSession,
    benchmark: str = 'SPY',
    history_days: int = 252
) -> dict:
    """
    提取指定 tickers 列表和 benchmark 的历史 K 线数据，
    拉取过去 (history_days + 100) 个交易日的数据，以确保充足的均线预热窗口。
    计算并返回它们各自的 RRG (相对轮动图) 轨迹有效数据。
    """
    tickers_to_fetch = set([t.upper() for t in tickers] + [benchmark.upper()])
    
    # 因为 AsyncSession 不支持在同一个 session 生命周期内真正的并发 execute，这里采用串行查询
    data_frames = {}
    
    # 扩大数据抓取边界：所需展现的历史天数 + 100 天滑动窗口前置预热天数
    fetch_limit = history_days + 100
    for t in tickers_to_fetch:
        stmt = select(DailyPrice).where(DailyPrice.ticker == t).order_by(desc(DailyPrice.date)).limit(fetch_limit)
        res = await db_session.execute(stmt)
        records = res.scalars().all()
        
        logger.info(f"RRG DEBUG: Fetched {len(records)} K-line records for ticker {t} from DB.")
        
        if not records:
            continue
            
        # 转换为 DataFrame，为了更好的对比精度，优先使用并提取调整后收盘价
        df = pd.DataFrame([{
            'date': r.date,
            'close': float(r.adjusted_close) if r.adjusted_close is not None else (float(r.close) if r.close is not None else 0.0)
        } for r in records])
        
        # 摘取出来的数据是倒序(desc)，反转回升序以匹配时序演变
        df = df.iloc[::-1].reset_index(drop=True)
        # 强转日期时间格式，保障 merge 正确对齐
        df['date'] = pd.to_datetime(df['date'])
        
        data_frames[t] = df

    benchmark_upper = benchmark.upper()
    current_time = pd.Timestamp.now().isoformat()
    
    if benchmark_upper not in data_frames:
        return {
            "benchmark": benchmark_upper,
            "update_time": current_time,
            "data": {}
        }
        
    benchmark_df = data_frames[benchmark_upper]
    
    SECTOR_MAP = {
        "XLK.US": "Technology", "XLF.US": "Financials", "XLV.US": "Health Care",
        "XLY.US": "Cons. Discret.", "XLP.US": "Cons. Staples", "XLE.US": "Energy",
        "XLI.US": "Industrials", "XLB.US": "Materials", "XLU.US": "Utilities",
        "XLRE.US": "Real Estate", "XLC.US": "Comm. Svcs"
    }
    
    rrg_data = {}
    for t in tickers:
        t_upper = t.upper()
        if t_upper == benchmark_upper or t_upper not in data_frames:
            continue
            
        ticker_df = data_frames[t_upper]
        
        # 将构造好的两份 DataFrame 交由 calculate_rrg 函数处理 (无需传入 tail_length，返回所有有效点)
        result = calculate_rrg(ticker_df, benchmark_df, window=14)
        
        if result:
            # 只截取用户最终所需的 history_days 长度发送回前端（计算好的最近一年）
            result = result[-history_days:]
            
            # 如果配置了映射名，使用更友好的行业名称作为客户端显示的键
            display_name = SECTOR_MAP.get(t_upper, t_upper)
            rrg_data[display_name] = result
            
    return {
        "benchmark": benchmark_upper,
        "update_time": current_time,
        "data": rrg_data
    }


