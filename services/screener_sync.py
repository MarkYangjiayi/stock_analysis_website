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

async def fetch_and_merge_bulk_data(target_date: str = None) -> pd.DataFrame:
    """
    并发抓取 Bulk EOD Prices 和 Bulk Fundamentals 并合并清洗为一个 Pandas DataFrame。
    """
    logger.info("Initializing concurrent EODHD bulk fetches...")
    
    # asyncio.gather concurrently awaits both HTTP endpoints
    prices_task = eodhd_client.get_bulk_eod_prices(exchange="US", date_str=target_date)
    fundamentals_task = eodhd_client.get_bulk_fundamentals(exchange="US")
    
    eod_data, fundamental_data = await asyncio.gather(prices_task, fundamentals_task)
    
    if not eod_data:
        raise ValueError("Failed to retrieve bulk EOD data.")
    if not fundamental_data:
        logger.warning("Failed to retrieve bulk fundamental data (possibly 403 Forbidden). Proceeding with EOD prices only.")
        fundamental_data = []
        
    logger.info(f"Retrieved {len(eod_data)} EOD records and {len(fundamental_data)} Fundamental records.")

    # EODHD eod bulk API has "code" for ticker name without exchange (e.g. "AAPL") and "exchange_short_name" (e.g. "US")
    # For daily prices bulk, the API returns objects: { "code": "AAPL", "date": "2024-10-10", "close": 150.0 ... }
    df_eod = pd.DataFrame(eod_data)
    
    # Only keep major US exchanges for standard screener (NYSE, NASDAQ, AMEX etc. are all bundled as US usually, but sometimes OTC is there too)
    if df_eod.empty or 'code' not in df_eod.columns:
        raise ValueError("EOD bulk data format error or empty.")

    # Synthesize the exact ticker symbol used in our DB (e.g. "AAPL.US")
    if 'exchange_short_name' in df_eod.columns:
        df_eod['ticker'] = df_eod['code'] + '.' + df_eod['exchange_short_name']
    else:
        df_eod['ticker'] = df_eod['code'] + '.US'
        
    # Bulk Fundamentals API returns key as Ticker without ".US" usually: {"AAPL": {"Sector": "...", "Industry": "...", "MarketCapitalization": 123...}, ...} 
    # OR returns list if requested via some interfaces. EODHD bulk-fundamentals typically returns an object with ticker as key.
    # We defensively handle both object and list shapes
    if isinstance(fundamental_data, dict) and fundamental_data:
        df_fund = pd.DataFrame.from_dict(fundamental_data, orient='index')
        df_fund['code'] = df_fund.index
    elif isinstance(fundamental_data, list) and fundamental_data:
        df_fund = pd.DataFrame(fundamental_data)
    else:
        df_fund = pd.DataFrame(columns=['code', 'Name', 'Sector', 'Industry', 'MarketCapitalization', 'PE', 'PB', 'DividendYield'])
        
    if not df_fund.empty and 'code' in df_fund.columns:
        df_fund['ticker'] = df_fund['code'] + '.US'
    else:
        df_fund['ticker'] = pd.Series(dtype='str')
    
    # Merge datasets on 'ticker' using LEFT JOIN so that we keep all prices even if fundamentals are missing
    logger.info("Merging EOD prices and fundamentals...")
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

        # Prepare mapping of basic columns depending on EODHD exact json keys
        # The dictionary extraction must be robust to missing keys
        def _safe_float(val):
            try:
                if pd.isna(val): return None
                return float(val)
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
            name = _safe_str(row.get('name')) or _safe_str(row.get('Name'))
            sector = _safe_str(row.get('Sector'))
            industry = _safe_str(row.get('Industry'))
            market_cap = _safe_float(row.get('MarketCapitalization') or row.get('market_capitalization'))
            pe = _safe_float(row.get('PE') or row.get('TrailingPE') or row.get('pe'))
            pb = _safe_float(row.get('PB') or row.get('PriceToBook'))
            yield_pct = _safe_float(row.get('DividendYield') or row.get('dividend_yield'))
            
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
                     stmt_dp = insert(DailyPrice).values(chunk)
                     stmt_dp = stmt_dp.on_conflict_do_update(
                         index_elements=['ticker', 'date'],
                         set_={"close": stmt_dp.excluded.close, "volume": stmt_dp.excluded.volume}
                     )
                     await db.execute(stmt_dp)
                     
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
                for record in chunk:
                    clean_record = {}
                    for k, v in record.items():
                        if pd.isna(v):
                            clean_record[k] = None
                        elif hasattr(v, 'item'):
                            clean_record[k] = v.item()
                        else:
                            clean_record[k] = v
                    clean_chunk.append(clean_record)
                
                stmt = insert(StockScreenerSnapshot).values(clean_chunk)
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
