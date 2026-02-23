import asyncio
import logging
import argparse
from datetime import datetime
from typing import List, Dict, Any

import pandas as pd
import pandas_ta_classic as ta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import select, and_, func

from models import StockScreenerSnapshot, DailyPrice, Ticker
from database import engine, async_session_maker
from services import eodhd_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

async def fetch_target_universe_fundamentals(tickers: set) -> list:
    """
    Fetch individual fundamental data for a set of tickers concurrently, 
    respecting EODHD rate limits via a Semaphore.
    """
    logger.info(f"Starting concurrent fetch for {len(tickers)} fundamental profiles...")
    
    semaphore = asyncio.Semaphore(15) # Safe concurrency limit for EODHD 100k tier
    results = []
    
    async def fetch_single(ticker: str):
        async with semaphore:
            data = await eodhd_client.get_fundamental_data(ticker)
            if data:
                # EODHD individual fundamental has a different structure:
                # General: { Code, Type, Name, Exchange, CurrencyCode... }
                # Highlights: { MarketCapitalization, PERatio, DividendYield... }
                # Valuation: { TrailingPE, ForwardPE, PriceBookMRQ... }
                
                gen = data.get("General", {})
                hl = data.get("Highlights", {})
                val = data.get("Valuation", {})
                fin = data.get("Financials", {})
                
                # FCF
                latest_cf = {}
                try: latest_cf = list(fin.get("Cash_Flow", {}).get("yearly", {}).values())[0]
                except: pass
                
                # Debt to Equity
                latest_bs = {}
                try: latest_bs = list(fin.get("Balance_Sheet", {}).get("quarterly", {}).values())[0]
                except: pass
                total_debt = hl.get("TotalDebt") or latest_bs.get("shortLongTermDebtTotal") or latest_bs.get("totalDebt")
                total_equity = latest_bs.get("totalStockholderEquity")
                debt_to_equity = None
                try: 
                     d = float(total_debt); e = float(total_equity)
                     if e > 0: debt_to_equity = d / e 
                except: pass
                
                # Gross Margin & Sales Growth
                inc_yearly = fin.get("Income_Statement", {}).get("yearly", {})
                latest_inc = {}
                try: latest_inc = list(inc_yearly.values())[0]
                except: pass
                
                gross_margin = None
                try:
                    gp = float(hl.get("GrossProfitTTM") or latest_inc.get("grossProfit") or 0)
                    rev = float(hl.get("RevenueTTM") or latest_inc.get("totalRevenue") or 0)
                    if rev > 0: gross_margin = gp / rev
                except: pass
                
                sales_growth_5yr = None
                try:
                    inc_vals = list(inc_yearly.values())
                    if len(inc_vals) >= 4:
                         idx = min(len(inc_vals) - 1, 4)
                         rev_new = float(inc_vals[0].get("totalRevenue") or 0)
                         rev_old = float(inc_vals[idx].get("totalRevenue") or 0)
                         if rev_old > 0 and rev_new > 0:
                              sales_growth_5yr = (rev_new / rev_old) ** (1/idx) - 1
                except: pass
                
                results.append({
                    "code": gen.get("Code", ticker.split('.')[0]),
                    "exchange": gen.get("Exchange", "US"),
                    "ticker": ticker,
                    "Name": gen.get("Name"),
                    "Sector": gen.get("Sector"),
                    "Industry": gen.get("Industry"),
                    "MarketCapitalization": hl.get("MarketCapitalization"),
                    "PERatio": hl.get("PERatio") or val.get("TrailingPE"),
                    "PriceToBook": val.get("PriceBookMRQ"),
                    "DividendYield": hl.get("DividendYield"),
                    "ROE": hl.get("ReturnOnEquityTTM"),
                    "DebtToEquity": debt_to_equity,
                    "FCF": latest_cf.get("freeCashFlow"),
                    "GrossMargin": gross_margin,
                    "SalesGrowth5yr": sales_growth_5yr
                })

    tasks = [fetch_single(t) for t in tickers]
    
    # Run tasks with progress logging
    total_tasks = len(tasks)
    chunk_size = 500
    for i in range(0, total_tasks, chunk_size):
        chunk = tasks[i:i+chunk_size]
        await asyncio.gather(*chunk)
        logger.info(f"Fetched fundamentals: {min(i+chunk_size, total_tasks)} / {total_tasks}")
        
    return results

async def fetch_and_merge_bulk_data(target_date: str = None) -> pd.DataFrame:
    """
    1. Fetch Index Constituents for S&P 500 and Russell 2000.
    2. Concurrently fetch bulk EOD closing prices for all US stocks.
    3. Filter bulk prices to only our target index constituents.
    4. Concurrently fetch detailed fundamentals for the target constituents INDIVIDUALLY to save costs.
    5. Merge and return.
    """
    logger.info("Fetching target index universes (S&P 500 and Russell 2000)...")
    
    sp500_task = eodhd_client.get_index_components("GSPC.INDX")
    russell_task = eodhd_client.get_index_components("RUT.INDX")
    
    sp500_tickers, russell_tickers = await asyncio.gather(sp500_task, russell_task)
    target_tickers = set(sp500_tickers + russell_tickers)
    logger.info(f"Total unique target tickers from S&P 500 and Russell 2000: {len(target_tickers)}")

    # Fetch daily bulk prices (still free/fast)
    eod_data = await eodhd_client.get_bulk_eod_prices(exchange="US", date_str=target_date)
    
    if not eod_data:
        raise ValueError("Failed to retrieve bulk EOD data.")

    df_eod = pd.DataFrame(eod_data)
    
    if df_eod.empty or 'code' not in df_eod.columns:
        raise ValueError("EOD bulk data format error or empty.")
        
    if 'exchange_short_name' in df_eod.columns:
        df_eod['ticker'] = df_eod['code'] + '.' + df_eod['exchange_short_name']
    else:
        df_eod['ticker'] = df_eod['code'] + '.US'
        
    # HUGE OPTIMIZATION: Discard all prices that are NOT in our target index universe
    df_eod = df_eod[df_eod['ticker'].isin(target_tickers)]
    logger.info(f"Filtered EOD prices down to {len(df_eod)} target index constituents.")

    # Now fetch fundamental data individually for our optimized target list
    fundamental_data = await fetch_target_universe_fundamentals(target_tickers)
    
    if fundamental_data:
        df_fund = pd.DataFrame(fundamental_data)
    else:
        df_fund = pd.DataFrame(columns=['ticker', 'Name', 'Sector', 'Industry', 'MarketCapitalization', 'PERatio', 'PriceToBook', 'DividendYield', 'ROE', 'DebtToEquity', 'FCF', 'GrossMargin', 'SalesGrowth5yr'])
        
    # Merge datasets on 'ticker'
    logger.info("Merging targeted EOD prices and fundamentals...")
    df_merged = pd.merge(df_eod, df_fund, on="ticker", how="left")
    
    return df_merged

async def calculate_technicals_locally(db: AsyncSession, tickers: List[str]) -> pd.DataFrame:
    """
    Since bulk API only returns 1 day of data, we need 60+ days of history to compute MA20/MA50/RSI.
    This function pulls all necessary recent history from our local PostgreSQL `daily_prices` table.
    """
    logger.info("Fetching recent local daily prices for technical indicator computations...")
    
    # Fetch recent past 100 max days for technical grouping
    # For 6000 stocks, 100 days is ~60k rows. Doing this locally is extremely fast.
    records = []
    for i in range(0, len(tickers), 5000):
        chunk = tickers[i:i+5000]
        stmt = select(DailyPrice.ticker, DailyPrice.date, DailyPrice.close).where(
            DailyPrice.ticker.in_(chunk)
        ).order_by(DailyPrice.date.asc())
        
        result = await db.execute(stmt)
        records.extend(result.all())
    
    if not records:
         return pd.DataFrame()
         
    df_hist = pd.DataFrame(records, columns=['ticker', 'date', 'close'])
    df_hist['close'] = df_hist['close'].astype(float)
    
    # Compute using Pandas GroupBy and pandas_ta
    logger.info("Calculating MA20, MA50, RSI locally...")
    
    # Vectorized fast computing by stock
    def compute_ta(group):
        if len(group) < 14:
            return pd.Series({'ma20': None, 'ma50': None, 'rsi_14': None, 'latest_close': group['close'].iloc[-1]})
        
        c = group['close']
        ma20 = c.rolling(20).mean().iloc[-1]
        ma50 = c.rolling(50).mean().iloc[-1]
        rsi = ta.rsi(c, length=14)
        rsi_val = rsi.iloc[-1] if rsi is not None and not rsi.empty else None
        
        return pd.Series({
            'ma20': ma20,
            'ma50': ma50,
            'rsi_14': rsi_val,
        })
        
    df_tech = df_hist.groupby('ticker').apply(compute_ta).reset_index()
    return df_tech


async def run_screener_pipeline(target_date: str = None):
    """
    主管道：串联获取并入库截面快照
    """
    try:
        # 1. Fetch cross-sectional daily bulk
        df_merged = await fetch_and_merge_bulk_data(target_date)
        if df_merged.empty:
            logger.warning("Merged dataset is empty. Skipping.")
            return
            
        # VERY IMPORTANT: EODHD bulk sometimes returns overlapping duplicates for the same day
        df_merged = df_merged.drop_duplicates(subset=['ticker'])

        # Prepare mapping of basic columns depending on EODHD exact json keys
        # The dictionary extraction must be robust to missing keys
        def _safe_float(val):
            try:
                if pd.isna(val) or pd.isnull(val): return None
                fval = float(val)
                import math
                if math.isinf(fval) or math.isnan(fval):
                    return None
                return fval
            except:
                return None
                
        def _safe_str(val):
            if pd.isna(val) or val == 'nan': return None
            return str(val).strip() or None

        records_to_upsert = []
        for index, row in df_merged.iterrows():
            # Safely unpack row
            ticker = row.get('ticker')
            
            # EOD Fields
            date_val = row.get('date')
            try:
                dt_val = datetime.strptime(str(date_val), '%Y-%m-%d').date()
            except:
                continue
                
            close_price = _safe_float(row.get('close'))
            volume_num = row.get('volume')
            
            # Fundamentals Fields
            name = _safe_str(row.get('name')) or _safe_str(row.get('Name')) or _safe_str(row.get('Company'))
            sector = _safe_str(row.get('Sector')) or _safe_str(row.get('sector'))
            industry = _safe_str(row.get('Industry')) or _safe_str(row.get('industry'))
            market_cap = _safe_float(row.get('MarketCapitalization')) or _safe_float(row.get('market_capitalization')) or _safe_float(row.get('MarketCap'))
            pe = _safe_float(row.get('PERatio')) or _safe_float(row.get('PE')) or _safe_float(row.get('TrailingPE')) or _safe_float(row.get('pe'))
            pb = _safe_float(row.get('PriceToBook')) or _safe_float(row.get('PB')) or _safe_float(row.get('PBRatio'))
            yield_pct = _safe_float(row.get('DividendYield')) or _safe_float(row.get('dividend_yield')) or _safe_float(row.get('Yield'))
            roe = _safe_float(row.get('ROE'))
            debt_to_equity = _safe_float(row.get('DebtToEquity'))
            fcf = _safe_float(row.get('FCF'))
            gross_margin = _safe_float(row.get('GrossMargin'))
            sales_growth_5yr = _safe_float(row.get('SalesGrowth5yr'))
            
            records_to_upsert.append({
                "ticker": ticker,
                "date": dt_val,
                "name": name,
                "sector": sector,
                "industry": industry,
                "market_cap": market_cap,
                "pe_ratio": pe,
                "pb_ratio": pb,
                "dividend_yield": yield_pct,
                "roe": roe,
                "debt_to_equity": debt_to_equity,
                "fcf": fcf,
                "gross_margin": gross_margin,
                "sales_growth_5yr": sales_growth_5yr,
                "close": close_price,
                "volume": int(volume_num) if pd.notna(volume_num) else None,
                "ma20": None,
                "ma50": None,
                "rsi_14": None
            })
            
        logger.info(f"Prepared {len(records_to_upsert)} base records for snapshot.")

        # 2. Database Transactions
        # Initialize DB Session
        async with async_session_maker() as db, db.begin():
            # First, ensure all tickers exist in Tickers table to avoid foreign key violations in DailyPrice
            ticker_list = list(set([r['ticker'] for r in records_to_upsert]))
            
            logger.info("Purging old screener data not in the current target universe...")
            from sqlalchemy import delete
            await db.execute(delete(StockScreenerSnapshot).where(StockScreenerSnapshot.ticker.not_in(ticker_list)))
            
            # Fetch existing tickers in chunks to avoid max bind parameter limits
            existing_tickers = set()
            for i in range(0, len(ticker_list), 5000):
                chunk = ticker_list[i:i+5000]
                existing_result = await db.execute(select(Ticker.ticker).where(Ticker.ticker.in_(chunk)))
                existing_tickers.update(row[0] for row in existing_result.all())
            
            missing_tickers = [t for t in ticker_list if t not in existing_tickers]
            if missing_tickers:
                logger.info(f"Inserting {len(missing_tickers)} missing tickers into the Tickers table...")
                missing_inserts = [{"ticker": t} for t in missing_tickers]
                for i in range(0, len(missing_inserts), 1000):
                    await db.execute(insert(Ticker).values(missing_inserts[i:i+1000]).on_conflict_do_nothing())
            
            # 2a. Sync these EOD prices to our DailyPrice history locally first!
            # Since technicals depend on this
            daily_price_inserts = []
            for r in records_to_upsert:
                 if r['close'] is not None:
                     daily_price_inserts.append({
                         "ticker": r['ticker'],
                         "date": r['date'],
                         "close": r['close'],
                         "adjusted_close": r['close'], # approx fallback
                         "volume": r['volume']
                     })
                     
            if daily_price_inserts:
                 # Chunking update
                 logger.info("Upserting latest EOD prices to local daily_prices table...")
                 for i in range(0, len(daily_price_inserts), 1000):
                     chunk = daily_price_inserts[i:i+1000]
                     stmt_dp = insert(DailyPrice)
                     stmt_dp = stmt_dp.on_conflict_do_update(
                         index_elements=['ticker', 'date'],
                         set_={"close": stmt_dp.excluded.close, "volume": stmt_dp.excluded.volume}
                     )
                     await db.execute(stmt_dp, chunk)
                     
            # 2b. Compute Local Technical Indicators
            df_technicals = await calculate_technicals_locally(db, ticker_list)
            
            # 2c. Merge Technicals into the snapshot models
            if not df_technicals.empty:
                df_technicals = df_technicals.drop_duplicates(subset=['ticker'])
                tech_map = df_technicals.set_index('ticker').to_dict('index')
                for r in records_to_upsert:
                     t_data = tech_map.get(r['ticker'])
                     if t_data:
                         r['ma20'] = _safe_float(t_data.get('ma20'))
                         r['ma50'] = _safe_float(t_data.get('ma50'))
                         r['rsi_14'] = _safe_float(t_data.get('rsi_14'))
            
            # 3. Final bulk Insert to StockScreenerSnapshot (Delete and Replace)
            logger.info("Starting Bulk Insert into StockScreenerSnapshot...")
            from sqlalchemy import delete
            target_dt_val = datetime.strptime(str(target_date or datetime.today().date()), '%Y-%m-%d').date()
            
            await db.execute(
                delete(StockScreenerSnapshot).where(StockScreenerSnapshot.date == target_dt_val)
            )
            
            chunk_size = 1000
            for i in range(0, len(records_to_upsert), chunk_size):
                chunk = records_to_upsert[i:i + chunk_size]
                
                clean_chunk = []
                import math
                for record in chunk:
                    clean_record = {}
                    for k, v in record.items():
                        if pd.isna(v):
                            clean_record[k] = None
                        elif isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
                            clean_record[k] = None
                        elif hasattr(v, 'item'):
                            # Extract native Python types from numpy wrappers
                            val = v.item()
                            if isinstance(val, float) and (math.isinf(val) or math.isnan(val)):
                                clean_record[k] = None
                            else:
                                clean_record[k] = val
                        else:
                            clean_record[k] = v
                    clean_chunk.append(clean_record)
                
                if len(clean_chunk) > 0:
                    stmt = insert(StockScreenerSnapshot).values(clean_chunk)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=['ticker', 'date'],
                        set_={
                            "name": stmt.excluded.name,
                            "sector": stmt.excluded.sector,
                            "industry": stmt.excluded.industry,
                            "market_cap": stmt.excluded.market_cap,
                            "pe_ratio": stmt.excluded.pe_ratio,
                            "pb_ratio": stmt.excluded.pb_ratio,
                            "dividend_yield": stmt.excluded.dividend_yield,
                            "roe": stmt.excluded.roe,
                            "debt_to_equity": stmt.excluded.debt_to_equity,
                            "fcf": stmt.excluded.fcf,
                            "gross_margin": stmt.excluded.gross_margin,
                            "sales_growth_5yr": stmt.excluded.sales_growth_5yr,
                            "close": stmt.excluded.close,
                            "volume": stmt.excluded.volume,
                            "ma20": stmt.excluded.ma20,
                            "ma50": stmt.excluded.ma50,
                            "rsi_14": stmt.excluded.rsi_14
                        }
                    )
                    await db.execute(stmt)
                
        logger.info(f"Successfully processed Screener snapshot job.")

    except Exception as e:
        logger.error(f"Screener Pipeline failed: {e}", exc_info=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the daily stock screener mass sync.")
    parser.add_argument("--date", type=str, help="Specific date string format YYYY-MM-DD", default=None)
    parser.add_argument("--backfill", type=int, help="Fetch bulk history for past N business days before running today's snapshot", default=0)
    args = parser.parse_args()
    
    async def main():
        if args.backfill > 0:
            logger.info(f"Starting Historical Backfill for {args.backfill} business days.")
            import pandas as pd
            end_dt = datetime.today()
            # Generate more business days than needed to account for holidays, then take the last N
            start_dt = end_dt - pd.Timedelta(days=args.backfill * 2) 
            b_days = pd.bdate_range(start=start_dt, end=end_dt)
            trading_days = b_days[-args.backfill:]
            
            for dt in trading_days:
                date_str = dt.strftime('%Y-%m-%d')
                logger.info(f"=== Running pipeline for backfilled date: {date_str} ===")
                await run_screener_pipeline(target_date=date_str)
        else:
            await run_screener_pipeline(target_date=args.date)
            
    asyncio.run(main())
