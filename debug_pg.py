import asyncio
from services.screener_sync import fetch_and_merge_bulk_data, calculate_technicals_locally
from database import async_session_maker
from models import StockScreenerSnapshot
from sqlalchemy import insert, delete
import math
import pandas as pd
from datetime import datetime
import sys

async def main():
    df_merged = await fetch_and_merge_bulk_data()
    async with async_session_maker() as db, db.begin():
        df_technicals = await calculate_technicals_locally(db, df_merged['ticker'].tolist())
    
    def _safe_float(val):
        try:
            if pd.isna(val) or pd.isnull(val): return None
            fval = float(val)
            if math.isinf(fval) or math.isnan(fval): return None
            return fval
        except: return None
        
    def _safe_str(val):
        if pd.isna(val) or val == 'nan': return None
        return str(val).strip() or None

    records_to_upsert = []
    for index, row in df_merged.iterrows():
        ticker = row.get('ticker')
        date_val = row.get('date')
        try: dt_val = datetime.strptime(str(date_val), '%Y-%m-%d').date()
        except: continue
        close_price = _safe_float(row.get('close'))
        volume_num = row.get('volume')
        
        name = _safe_str(row.get('name')) or _safe_str(row.get('Name')) or _safe_str(row.get('Company'))
        sector = _safe_str(row.get('Sector')) or _safe_str(row.get('sector'))
        industry = _safe_str(row.get('Industry')) or _safe_str(row.get('industry'))
        market_cap = _safe_float(row.get('MarketCapitalization')) or _safe_float(row.get('market_capitalization')) or _safe_float(row.get('MarketCap'))
        pe = _safe_float(row.get('PERatio')) or _safe_float(row.get('PE')) or _safe_float(row.get('TrailingPE')) or _safe_float(row.get('pe'))
        pb = _safe_float(row.get('PriceToBook')) or _safe_float(row.get('PB')) or _safe_float(row.get('PBRatio'))
        yield_pct = _safe_float(row.get('DividendYield')) or _safe_float(row.get('dividend_yield')) or _safe_float(row.get('Yield'))
        
        records_to_upsert.append({
            "ticker": ticker, "date": dt_val, "name": name, "sector": sector, "industry": industry,
            "market_cap": market_cap, "pe_ratio": pe, "pb_ratio": pb, "dividend_yield": yield_pct,
            "close": close_price, "volume": int(volume_num) if pd.notna(volume_num) else None,
            "ma20": None, "ma50": None, "rsi_14": None
        })
        
    if not df_technicals.empty:
        df_technicals = df_technicals.drop_duplicates(subset=['ticker'])
        tech_map = df_technicals.set_index('ticker').to_dict('index')
        for r in records_to_upsert:
             t_data = tech_map.get(r['ticker'])
             if t_data:
                 r['ma20'] = _safe_float(t_data.get('ma20'))
                 r['ma50'] = _safe_float(t_data.get('ma50'))
                 r['rsi_14'] = _safe_float(t_data.get('rsi_14'))
                 
    async with async_session_maker() as db:
        for r in records_to_upsert:
            try:
                # Do single insert in its own transaction block
                async with db.begin():
                    # clean dict
                    clean_record = {}
                    for k, v in r.items():
                        if pd.isna(v): clean_record[k] = None
                        elif isinstance(v, float) and (math.isinf(v) or math.isnan(v)): clean_record[k] = None
                        elif hasattr(v, 'item'):
                            val = v.item()
                            if isinstance(val, float) and (math.isinf(val) or math.isnan(val)): clean_record[k] = None
                            else: clean_record[k] = val
                        else: clean_record[k] = v
                        
                    await db.execute(delete(StockScreenerSnapshot).where(StockScreenerSnapshot.ticker == r['ticker']))
                    stmt = insert(StockScreenerSnapshot).values([clean_record])
                    await db.execute(stmt)
            except Exception as e:
                print(f"FAILED ROW: {r}")
                print(f"CLEAN RECORD: {clean_record}")
                print(f"ERROR: {e}")
                sys.exit(1)

    print("ALL OK")
asyncio.run(main())
