import asyncio
import logging
import argparse
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable, List, Dict, Any, Optional

import pandas as pd
import pandas_ta_classic as ta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy import select, and_, func

from models import StockScreenerSnapshot, DailyPrice, Ticker
from database import engine, async_session_maker
from services import eodhd_client
from services.data_quality import DataQualityError, validate_screener_records
from services.pipeline_runs import (
    begin_pipeline_run,
    finish_pipeline_run,
    publish_dataset,
    update_pipeline_run,
)
from services.security_master import bulk_upsert_securities
from services.universe import record_universe_membership
from services.raw_store import persist_snapshot
from services.data_sync import _upsert_financials
from core.config import settings
from core.time_utils import utc_now

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _validate_universe_coverage(target_tickers: set[str], priced_tickers: set[str]) -> float:
    if len(target_tickers) < settings.PIPELINE_MIN_UNIVERSE_SIZE:
        raise ValueError(
            f"Resolved target universe is too small: {len(target_tickers)} "
            f"< {settings.PIPELINE_MIN_UNIVERSE_SIZE}"
        )
    coverage = len(priced_tickers & target_tickers) / len(target_tickers)
    if coverage < settings.PIPELINE_MIN_UNIVERSE_COVERAGE:
        raise ValueError(
            f"Bulk price universe coverage {coverage:.2%} is below "
            f"{settings.PIPELINE_MIN_UNIVERSE_COVERAGE:.2%} "
            f"({len(priced_tickers)} priced / {len(target_tickers)} target)"
        )
    return coverage


async def fetch_target_universe_fundamentals(
    tickers: set,
    client=None,
    on_chunk: Optional[Callable[[Dict[str, dict]], Awaitable[None]]] = None,
) -> list:
    """
    Fetch individual fundamental data for a set of tickers concurrently, 
    respecting EODHD rate limits via a Semaphore.
    """
    logger.info(f"Starting concurrent fetch for {len(tickers)} fundamental profiles...")
    
    semaphore = asyncio.Semaphore(15) # Safe concurrency limit for EODHD 100k tier
    results = []
    raw_fundamentals = {}
    
    async def fetch_single(ticker: str):
        async with semaphore:
            data = await eodhd_client.get_fundamental_data(ticker, client=client)
            if data:
                raw_fundamentals[ticker] = data
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

    # Run tasks with progress logging
    symbols = sorted(tickers)
    total_tasks = len(symbols)
    # Fundamental payloads are large. Bound the retained raw batch so a full
    # Russell 2000 run cannot grow into multi-gigabyte process memory.
    chunk_size = 100
    for i in range(0, total_tasks, chunk_size):
        await asyncio.gather(*(fetch_single(ticker) for ticker in symbols[i:i+chunk_size]))
        if on_chunk and raw_fundamentals:
            await on_chunk(dict(raw_fundamentals))
        raw_fundamentals.clear()
        logger.info(f"Fetched fundamentals: {min(i+chunk_size, total_tasks)} / {total_tasks}")

    return results

async def fetch_and_merge_bulk_data(
    target_date: str = None,
    target_tickers: set = None,
    fundamental_chunk_handler: Optional[
        Callable[[Dict[str, dict], date], Awaitable[None]]
    ] = None,
) -> pd.DataFrame:
    """
    1. Fetch Index Constituents for S&P 500 and Russell 2000.
    2. Concurrently fetch bulk EOD closing prices for all US stocks.
    3. Filter bulk prices to only our target index constituents.
    4. Concurrently fetch detailed fundamentals for the target constituents INDIVIDUALLY to save costs.
    5. Merge and return.
    """
    logger.info("Fetching target index universes (S&P 500 and Russell 2000)...")

    async with eodhd_client.create_http_client() as client:
        if target_tickers is None:
            sp500_task = eodhd_client.get_index_components("GSPC.INDX", client=client)
            russell_task = eodhd_client.get_index_components("RUT.INDX", client=client)
            sp500_tickers, russell_tickers = await asyncio.gather(sp500_task, russell_task)
            target_tickers = set(sp500_tickers + russell_tickers)
        target_tickers = {ticker.upper() for ticker in target_tickers}
        logger.info(f"Total unique target tickers from S&P 500 and Russell 2000: {len(target_tickers)}")

        # Fetch daily bulk prices (still free/fast)
        eod_data = await eodhd_client.get_bulk_eod_prices(exchange="US", date_str=target_date, client=client)

        if not eod_data:
            raise ValueError("Failed to retrieve bulk EOD data.")

        df_eod = pd.DataFrame(eod_data)
        if df_eod.empty or 'code' not in df_eod.columns:
            raise ValueError("EOD bulk data format error or empty.")

        if 'exchange_short_name' in df_eod.columns:
            df_eod['ticker'] = df_eod['code'] + '.' + df_eod['exchange_short_name']
        else:
            df_eod['ticker'] = df_eod['code'] + '.US'

        # Discard all prices that are not in the observed target universe.
        df_eod = df_eod[df_eod['ticker'].isin(target_tickers)]
        logger.info(f"Filtered EOD prices down to {len(df_eod)} target index constituents.")
        priced_tickers = set(df_eod["ticker"])
        universe_coverage = _validate_universe_coverage(target_tickers, priced_tickers)

        observed_dates = set(pd.to_datetime(df_eod["date"], errors="coerce").dropna().dt.date)
        if len(observed_dates) != 1:
            raise ValueError(f"Expected one EOD observation date, found {sorted(observed_dates)}")
        observed_date = next(iter(observed_dates))
        if target_date and observed_date != datetime.strptime(target_date, "%Y-%m-%d").date():
            raise ValueError(f"Provider returned {observed_date} for requested date {target_date}")

        # Reuse the same connection pool for the large fundamental batch.
        async def handle_chunk(raw_batch: Dict[str, dict]) -> None:
            if fundamental_chunk_handler:
                await fundamental_chunk_handler(raw_batch, observed_date)

        fundamental_data = await fetch_target_universe_fundamentals(
            priced_tickers,
            client=client,
            on_chunk=handle_chunk,
        )

    if fundamental_data:
        df_fund = pd.DataFrame(fundamental_data)
    else:
        df_fund = pd.DataFrame(columns=['ticker', 'Name', 'Sector', 'Industry', 'MarketCapitalization', 'PERatio', 'PriceToBook', 'DividendYield', 'ROE', 'DebtToEquity', 'FCF', 'GrossMargin', 'SalesGrowth5yr'])
        
    # Merge datasets on 'ticker'
    logger.info("Merging targeted EOD prices and fundamentals...")
    df_merged = pd.merge(df_eod, df_fund, on="ticker", how="left")
    df_merged.attrs["target_tickers"] = sorted(target_tickers)
    df_merged.attrs["priced_tickers"] = sorted(priced_tickers)
    df_merged.attrs["universe_coverage"] = universe_coverage
    
    return df_merged

async def calculate_technicals_locally(
    db: AsyncSession,
    tickers: List[str],
    as_of_date: date = None,
) -> pd.DataFrame:
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
        conditions = [DailyPrice.ticker.in_(chunk)]
        if as_of_date:
            conditions.extend([
                DailyPrice.date <= as_of_date,
                DailyPrice.date >= as_of_date - timedelta(days=400),
            ])
        stmt = select(DailyPrice.ticker, DailyPrice.date, DailyPrice.close).where(
            *conditions
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
            return pd.Series({'ma20': None, 'ma50': None, 'rsi_14': None})
        
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
        
    df_tech = df_hist.groupby('ticker').apply(compute_ta, include_groups=False).reset_index()
    return df_tech


async def run_screener_pipeline(target_date: str = None, observe_current_universe: bool = False):
    """
    主管道：串联获取并入库截面快照
    """
    requested_date = datetime.strptime(target_date, "%Y-%m-%d").date() if target_date else None
    run_id = await begin_pipeline_run("daily_screener", requested_date)
    try:
        await update_pipeline_run(run_id, "resolving_universe")
        historical_universe = None
        if requested_date and requested_date < date.today() and not observe_current_universe:
            raise ValueError(
                "Historical screener snapshots cannot be reconstructed from current fundamentals. "
                "Load an archived point-in-time source payload instead."
            )

        # 1. Fetch cross-sectional daily bulk
        await update_pipeline_run(run_id, "fetching_source_data")
        async def persist_fundamental_chunk(raw_batch: Dict[str, dict], observed_date: date) -> None:
            async with async_session_maker() as raw_db, raw_db.begin():
                await raw_db.execute(
                    insert(Ticker)
                    .values([{"ticker": ticker} for ticker in raw_batch])
                    .on_conflict_do_nothing(index_elements=["ticker"])
                )
                for ticker, raw_payload in raw_batch.items():
                    raw_snapshot = await persist_snapshot(
                        raw_db,
                        "EODHD",
                        "fundamentals",
                        raw_payload,
                        as_of_date=observed_date,
                        details={"ticker": ticker},
                    )
                    await _upsert_financials(
                        ticker,
                        raw_payload,
                        raw_db,
                        raw_snapshot_id=raw_snapshot.id,
                    )

        df_merged = await fetch_and_merge_bulk_data(
            target_date,
            target_tickers=historical_universe,
            fundamental_chunk_handler=persist_fundamental_chunk,
        )
        if df_merged.empty:
            raise ValueError("Merged screener dataset is empty")
        target_universe = set(df_merged.attrs.get("target_tickers", df_merged["ticker"]))
            
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

        if not records_to_upsert:
            raise ValueError("No valid screener records were produced")
        snapshot_dates = {record["date"] for record in records_to_upsert}
        if len(snapshot_dates) != 1:
            raise ValueError(f"Expected one snapshot date, found {sorted(snapshot_dates)}")
        snapshot_date = next(iter(snapshot_dates))
        if requested_date and snapshot_date != requested_date:
            raise ValueError(f"Provider returned {snapshot_date} for requested date {requested_date}")

        # 2. Database Transactions
        # Initialize DB Session
        await update_pipeline_run(run_id, "writing_prices", len(records_to_upsert))
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

            ticker_profiles = [
                {
                    "ticker": record["ticker"],
                    "name": record.get("name"),
                    "sector": record.get("sector"),
                    "industry": record.get("industry"),
                    "currency": "USD",
                    "last_updated": utc_now(),
                }
                for record in records_to_upsert
            ]
            for i in range(0, len(ticker_profiles), 500):
                profile_stmt = insert(Ticker).values(ticker_profiles[i:i + 500])
                profile_stmt = profile_stmt.on_conflict_do_update(
                    index_elements=["ticker"],
                    set_={
                        "name": profile_stmt.excluded.name,
                        "sector": profile_stmt.excluded.sector,
                        "industry": profile_stmt.excluded.industry,
                        "currency": profile_stmt.excluded.currency,
                        "last_updated": profile_stmt.excluded.last_updated,
                    },
                )
                await db.execute(profile_stmt)

            await bulk_upsert_securities(db, records_to_upsert, snapshot_date)
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
            df_technicals = await calculate_technicals_locally(db, ticker_list, as_of_date=snapshot_date)
            
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

            quality_report = validate_screener_records(records_to_upsert)
            if not quality_report.passed:
                raise DataQualityError(quality_report)
            await record_universe_membership(
                db,
                universe="SP500_RUSSELL2000",
                tickers=target_universe,
                effective_date=snapshot_date,
                source_run_id=run_id,
                minimum_retained_fraction=settings.PIPELINE_MIN_UNIVERSE_COVERAGE,
            )
            
            # 3. Final bulk Insert to StockScreenerSnapshot (Delete and Replace)
            logger.info("Starting Bulk Insert into StockScreenerSnapshot...")
            from sqlalchemy import delete
            await db.execute(
                delete(StockScreenerSnapshot).where(StockScreenerSnapshot.date == snapshot_date)
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
                
        await update_pipeline_run(run_id, "validating_and_publishing", len(records_to_upsert))
        await publish_dataset("screener", snapshot_date, run_id)
        await finish_pipeline_run(run_id, "published", quality_report=quality_report.to_dict())
        logger.info("Successfully processed Screener snapshot job.")
        return {"run_id": run_id, "status": "published", "as_of_date": snapshot_date.isoformat(), "quality": quality_report.to_dict()}

    except asyncio.CancelledError:
        await finish_pipeline_run(run_id, "cancelled", error_message="Pipeline execution was cancelled")
        raise
    except Exception as e:
        logger.error(f"Screener Pipeline failed: {e}", exc_info=True)
        report = e.report.to_dict() if isinstance(e, DataQualityError) else None
        await finish_pipeline_run(run_id, "failed", quality_report=report, error_message=str(e))
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the daily stock screener mass sync.")
    parser.add_argument("--date", type=str, help="Specific date string format YYYY-MM-DD", default=None)
    parser.add_argument("--backfill", type=int, help="Fetch bulk history for past N business days before running today's snapshot", default=0)
    args = parser.parse_args()
    
    async def main():
        if args.backfill > 0:
            raise ValueError(
                "--backfill is disabled because the provider endpoint returns current fundamentals. "
                "Use archived point-in-time raw snapshots for historical screener reconstruction."
            )
        else:
            await run_screener_pipeline(target_date=args.date)
            
    asyncio.run(main())


async def refresh_screener_technicals(snapshot_date: date) -> int:
    """Recompute a published snapshot after cold-start price history is loaded."""
    from sqlalchemy import update

    async with async_session_maker() as db:
        result = await db.execute(
            select(StockScreenerSnapshot.ticker).where(StockScreenerSnapshot.date == snapshot_date)
        )
        tickers = list(result.scalars().all())
        technicals = await calculate_technicals_locally(db, tickers, as_of_date=snapshot_date)
        if technicals.empty:
            return 0
        updated = 0
        async with db.begin_nested():
            for row in technicals.to_dict("records"):
                await db.execute(
                    update(StockScreenerSnapshot)
                    .where(
                        StockScreenerSnapshot.ticker == row["ticker"],
                        StockScreenerSnapshot.date == snapshot_date,
                    )
                    .values(
                        ma20=None if pd.isna(row.get("ma20")) else float(row["ma20"]),
                        ma50=None if pd.isna(row.get("ma50")) else float(row["ma50"]),
                        rsi_14=None if pd.isna(row.get("rsi_14")) else float(row["rsi_14"]),
                    )
                )
                updated += 1
        await db.commit()
        return updated
