import logging
from datetime import datetime
from typing import Optional

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.sqlite import insert

from models import Ticker, DailyPrice, FinancialStatement
from services import eodhd_client

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------
# 数据同步服务核心逻辑
# ------------------------------------------------------------------------

async def sync_ticker_data(ticker: str, db: AsyncSession) -> bool:
    """
    业务逻辑方法: 同步冷启动或后台任务触发的单只股票数据
    根据架构文档的数据流转设计:
    - 外部数据获取
    - 使用 pandas 清洗日 K 线数据
    - 入库 (PostgreSQL DB)，使用 on_conflict_do_update 解决唯一索引冲突
    """
    logger.info(f"Starting data sync for {ticker}")

    #并发拉取基本面和历史日 K 线数据
    #这里为了满足单实例 httpx 限制，由于 EODHD 内部使用独立连接获取，这没有问题
    fundamentals = await eodhd_client.get_fundamental_data(ticker)
    eod_data = await eodhd_client.get_eod_historical_data(ticker)

    if not fundamentals or not eod_data:
        logger.error(f"Failed to fetch data for {ticker}. Aborting DB sync.")
        return False
        
    try:
        # 1. 插入或更新基础股票信息 (Tickers 表)
        await _upsert_ticker_info(ticker, fundamentals, db)

        # 2. 插入或更新财务信息 (FinancialStatements 表)
        await _upsert_financials(ticker, fundamentals, db)
        
        # 3. 使用 pandas 清洗和入库历史 K 线数据 (DailyPrices 表)
        await _upsert_daily_prices(ticker, eod_data, db)

        # 提交整个会话 (事务控制)
        await db.commit()
        logger.info(f"Data sync for {ticker} completed successfully.")
        return True

    except Exception as e:
        await db.rollback()
        logger.error(f"Error occurred during data sync for {ticker}: {e}", exc_info=True)
        return False


# ------------------------------------------------------------------------
# 子模块封装提取
# ------------------------------------------------------------------------

async def _upsert_ticker_info(ticker: str, fundamentals: dict, db: AsyncSession):
    general_info = fundamentals.get("General", {})
    
    stmt = insert(Ticker).values(
        ticker=ticker,
        name=general_info.get("Name"),
        exchange=general_info.get("Exchange"),
        sector=general_info.get("Sector"),
        industry=general_info.get("Industry"),
        description=general_info.get("Description"),
        currency=general_info.get("CurrencyCode"),
        last_updated=datetime.utcnow()
    )

    # 遇到主键冲突时，只更新基础字段及最后更新时间
    update_dict = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        index_elements=['ticker'],
        set_={
            "name": update_dict.name,
            "exchange": update_dict.exchange,
            "sector": update_dict.sector,
            "industry": update_dict.industry,
            "description": update_dict.description,
            "currency": update_dict.currency,
            "last_updated": update_dict.last_updated
        }
    )
    
    await db.execute(stmt)


async def _upsert_financials(ticker: str, fundamentals: dict, db: AsyncSession):
    """
    解析 JSONB 数据并插入/更新 financial_statements 表。
    仅取财务数据表 (Financials.Income_Statement.yearly 等)，根据业务按需扩展
    """
    def _safe_float(val) -> Optional[float]:
        try:
            if val is None:
                return None
            return float(val)
        except ValueError:
            return None

    financials_data = fundamentals.get("Financials", {})
    
    insert_values = []
    
    for period_key, period_name in [("yearly", "Yearly"), ("quarterly", "Quarterly")]:
        income_statement = financials_data.get("Income_Statement", {}).get(period_key, {})
        balance_sheet = financials_data.get("Balance_Sheet", {}).get(period_key, {})
        cash_flow = financials_data.get("Cash_Flow", {}).get(period_key, {})
        
        # 以 Income_Statement 的键 (日期) 作为核心维度遍历
        # 如果有的日期仅在其它的表中存在，需要做并集处理
        all_dates = set(income_statement.keys()) | set(balance_sheet.keys()) | set(cash_flow.keys())

        for str_date in all_dates:
            # 转为标准的 datetime.date
            try:
                fiscal_date = datetime.strptime(str_date, "%Y-%m-%d").date()
            except ValueError:
                continue
            
            # 尝试提取独立核心指标
            inc_stmt_entry = income_statement.get(str_date, {})
            
            # 注意清洗，可能为 None 或非数值
            revenue_val = inc_stmt_entry.get("totalRevenue")
            net_inc_val = inc_stmt_entry.get("netIncome")

            insert_values.append({
                "ticker": ticker,
                "fiscal_date": fiscal_date,
                "period": period_name,
                "revenue": _safe_float(revenue_val),
                "net_income": _safe_float(net_inc_val),
                "income_statement": inc_stmt_entry or None,
                "balance_sheet": balance_sheet.get(str_date, {}) or None,
                "cash_flow": cash_flow.get(str_date, {}) or None
            })

    if not insert_values:
        return

    stmt = insert(FinancialStatement).values(insert_values)
    update_dict = stmt.excluded

    stmt = stmt.on_conflict_do_update(
        index_elements=['ticker', 'fiscal_date', 'period'],
        set_={
            "revenue": update_dict.revenue,
            "net_income": update_dict.net_income,
            "income_statement": update_dict.income_statement,
            "balance_sheet": update_dict.balance_sheet,
            "cash_flow": update_dict.cash_flow,
        }
    )
    
    await db.execute(stmt)


async def _upsert_daily_prices(ticker: str, eod_data: list, db: AsyncSession):
    """
    使用 pandas 清洗日 K 线数据并批量写入，避免主键冲突
    """
    df = pd.DataFrame(eod_data)
    
    if df.empty:
        return

    # 1. 确保必要的列都存在并转换为对应类型
    if 'date' not in df.columns:
        return

    df['ticker'] = ticker
    
    # 2. 转换数据类型，并自动处理缺失值
    df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.date
    
    # EODHD API 的返回键值一般小写，比如 open, high, low, close, adjusted_close, volume
    numeric_cols = ['open', 'high', 'low', 'close', 'adjusted_close', 'volume']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        else:
            df[col] = None

    # 清除日期转换失败的无效行
    df = df.dropna(subset=['date'])
    
    # 填充缺失值为 None
    # Pandas 使用 NaN, SQLAlchemy 需要 Python's None
    df = df.replace({pd.NA: None, float('nan'): None})

    # 将 DataFrame 转换为 List[Dict] 以供 SQLAlchemy 批量插入
    # 仅收集需要的列
    insert_cols = ['ticker', 'date', 'open', 'high', 'low', 'close', 'adjusted_close', 'volume']
    records = df[insert_cols].to_dict('records')

    if not records:
        return

    # 进行 Chunking，避免 PostgreSQL 超过 32767 参数限制
    chunk_size = 2000
    for i in range(0, len(records), chunk_size):
        chunk = records[i:i + chunk_size]
        
        stmt = insert(DailyPrice).values(chunk)
        
        # 解决唯一索引冲突：如果相同股票相同日期的记录已存在，则进行覆盖更新
        update_dict = stmt.excluded
        stmt = stmt.on_conflict_do_update(
            index_elements=['ticker', 'date'],
            set_={
                "open": update_dict.open,
                "high": update_dict.high,
                "low": update_dict.low,
                "close": update_dict.close,
                "adjusted_close": update_dict.adjusted_close,
                "volume": update_dict.volume,
            }
        )
        
        await db.execute(stmt)
