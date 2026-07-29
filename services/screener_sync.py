import asyncio
import logging
import argparse
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable, List, Dict, Any, Optional

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy import select, and_, func

from models import StockScreenerSnapshot, DailyPrice, Ticker, CorporateAction
from database import engine, async_session_maker
from services import eodhd_client
from services.data_quality import DataQualityError, validate_screener_records
from services.pipeline_runs import (
    begin_pipeline_run,
    finish_pipeline_run,
    publish_datasets_and_finish,
    update_pipeline_run,
)
from services.corporate_actions import upsert_corporate_actions
from services.security_master import bulk_upsert_securities
from services.universe import record_universe_membership
from services.raw_store import persist_snapshot
from services.data_sync import _upsert_financials
from services.screener_metrics import (
    calculate_dividend_growth,
    calculate_price_metrics,
    extract_fundamental_metrics,
)
from core.config import settings
from core.time_utils import utc_now

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

TECHNICAL_SNAPSHOT_FIELDS = (
    "ma20",
    "ma50",
    "ma200",
    "rsi_14",
    "average_volume_3m",
    "relative_volume",
    "performance_1d",
    "performance_1w",
    "performance_1m",
    "performance_3m",
    "performance_6m",
    "performance_ytd",
    "performance_1yr",
    "volatility_1w",
    "volatility_1m",
    "gap",
    "change_from_open",
    "high_20d_rel",
    "low_20d_rel",
    "high_50d_rel",
    "low_50d_rel",
    "high_52w_rel",
    "low_52w_rel",
    "beta_1yr",
    "atr_14",
    "candlestick",
)


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
                gen = data.get("General", {})
                metrics = extract_fundamental_metrics(data)
                results.append({
                    "code": gen.get("Code", ticker.split('.')[0]),
                    "ticker": ticker,
                    "Name": gen.get("Name"),
                    "Sector": gen.get("Sector"),
                    "Industry": gen.get("Industry"),
                    **metrics,
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
        sp500_tickers: list[str] = []
        russell_tickers: list[str] = []
        if target_tickers is None:
            sp500_task = eodhd_client.get_index_components("GSPC.INDX", client=client)
            russell_task = eodhd_client.get_index_components("RUT.INDX", client=client)
            sp500_tickers, russell_tickers = await asyncio.gather(sp500_task, russell_task)
            target_tickers = set(sp500_tickers + russell_tickers)
        target_tickers = {ticker.upper() for ticker in target_tickers}
        logger.info(f"Total unique target tickers from S&P 500 and Russell 2000: {len(target_tickers)}")

        # Fetch the daily market batch and matching corporate actions. Each is
        # one exchange-wide request, avoiding thousands of per-symbol calls.
        eod_data, split_data, dividend_data = await asyncio.gather(
            eodhd_client.get_bulk_eod_prices(exchange="US", date_str=target_date, client=client),
            eodhd_client.get_bulk_corporate_actions(
                "splits", exchange="US", date_str=target_date, client=client
            ),
            eodhd_client.get_bulk_corporate_actions(
                "dividends", exchange="US", date_str=target_date, client=client
            ),
        )

        if not isinstance(eod_data, list) or not eod_data:
            raise ValueError("Failed to retrieve bulk EOD data.")
        if not isinstance(split_data, list) or not isinstance(dividend_data, list):
            raise ValueError("Failed to retrieve bulk corporate actions.")

        df_eod = pd.DataFrame(eod_data)
        if df_eod.empty or 'code' not in df_eod.columns:
            raise ValueError("EOD bulk data format error or empty.")

        if 'exchange_short_name' in df_eod.columns:
            df_eod['ticker'] = df_eod['code'] + '.' + df_eod['exchange_short_name']
        else:
            df_eod['ticker'] = df_eod['code'] + '.US'

        benchmark_prices = df_eod[
            df_eod["code"].astype(str).str.upper() == "SPY"
        ].copy()
        if not benchmark_prices.empty:
            benchmark_prices["ticker"] = "SPY.US"

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
        df_fund = pd.DataFrame(columns=["ticker", "Name", "Sector", "Industry"])
        
    # Merge datasets on 'ticker'
    logger.info("Merging targeted EOD prices and fundamentals...")
    df_merged = pd.merge(df_eod, df_fund, on="ticker", how="left")
    df_merged.attrs["target_tickers"] = sorted(target_tickers)
    df_merged.attrs["sp500_tickers"] = sorted({ticker.upper() for ticker in sp500_tickers})
    df_merged.attrs["russell2000_tickers"] = sorted({ticker.upper() for ticker in russell_tickers})
    df_merged.attrs["priced_tickers"] = sorted(priced_tickers)
    df_merged.attrs["universe_coverage"] = universe_coverage
    df_merged.attrs["benchmark_prices"] = benchmark_prices.to_dict("records")
    # Preserve the exact exchange-wide responses for immutable lineage. The
    # normalized action lists below remain limited to the target universe.
    df_merged.attrs["raw_bulk_eod"] = eod_data
    df_merged.attrs["raw_bulk_splits"] = split_data
    df_merged.attrs["raw_bulk_dividends"] = dividend_data

    def action_ticker(item: dict) -> Optional[str]:
        code = str(item.get("code") or "").strip().upper()
        if not code:
            return None
        if "." in code:
            return code
        exchange = str(
            item.get("exchange_short_name") or item.get("exchange") or "US"
        ).strip().upper()
        return f"{code}.{exchange}"

    df_merged.attrs["bulk_splits"] = [
        {**item, "ticker": ticker}
        for item in split_data
        if isinstance(item, dict) and (ticker := action_ticker(item)) in target_tickers
    ]
    df_merged.attrs["bulk_dividends"] = [
        {**item, "ticker": ticker}
        for item in dividend_data
        if isinstance(item, dict) and (ticker := action_ticker(item)) in target_tickers
    ]
    
    return df_merged

async def calculate_technicals_locally(
    db: AsyncSession,
    tickers: List[str],
    as_of_date: date = None,
) -> pd.DataFrame:
    """Compute the supported technical screen from point-in-time local OHLCV."""
    logger.info("Fetching recent local daily prices for technical indicator computations...")
    
    records = []
    for i in range(0, len(tickers), 5000):
        chunk = tickers[i:i+5000]
        conditions = [DailyPrice.ticker.in_(chunk)]
        if as_of_date:
            conditions.extend([
                DailyPrice.date <= as_of_date,
                DailyPrice.date >= as_of_date - timedelta(days=400),
            ])
        stmt = select(
            DailyPrice.ticker,
            DailyPrice.date,
            DailyPrice.open,
            DailyPrice.high,
            DailyPrice.low,
            DailyPrice.close,
            DailyPrice.adjusted_close,
            DailyPrice.volume,
        ).where(
            *conditions
        ).order_by(DailyPrice.date.asc())
        
        result = await db.execute(stmt)
        records.extend(result.all())
    
    if not records:
         return pd.DataFrame()
         
    df_hist = pd.DataFrame(
        records,
        columns=["ticker", "date", "open", "high", "low", "close", "adjusted_close", "volume"],
    )
    for column in ("open", "high", "low", "close", "adjusted_close", "volume"):
        df_hist[column] = pd.to_numeric(df_hist[column], errors="coerce")
    df_hist["adjusted_close"] = df_hist["adjusted_close"].fillna(df_hist["close"])

    benchmark_result = await db.execute(
        select(
            DailyPrice.date,
            DailyPrice.close,
            DailyPrice.adjusted_close,
        )
        .where(
            DailyPrice.ticker == "SPY.US",
            DailyPrice.date <= (as_of_date or date.today()),
            DailyPrice.date >= (as_of_date or date.today()) - timedelta(days=400),
        )
        .order_by(DailyPrice.date.asc())
    )
    benchmark_rows = benchmark_result.all()
    benchmark_returns = None
    benchmark_as_of = as_of_date or date.today()
    if benchmark_rows and benchmark_rows[-1].date == benchmark_as_of:
        benchmark = pd.DataFrame(benchmark_rows, columns=["date", "close", "adjusted_close"])
        benchmark["adjusted_close"] = pd.to_numeric(
            benchmark["adjusted_close"], errors="coerce"
        ).fillna(pd.to_numeric(benchmark["close"], errors="coerce"))
        benchmark_returns = pd.Series(
            benchmark["adjusted_close"].pct_change().values,
            index=pd.to_datetime(benchmark["date"]),
        ).dropna()

    logger.info("Calculating expanded screener technicals locally...")
    output = []
    for ticker, group in df_hist.groupby("ticker"):
        if as_of_date is not None and group["date"].max() != as_of_date:
            continue
        output.append({"ticker": ticker, **calculate_price_metrics(group, benchmark_returns)})
    return pd.DataFrame(output)


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
        sp500_universe = set(df_merged.attrs.get("sp500_tickers", []))
        russell2000_universe = set(df_merged.attrs.get("russell2000_tickers", []))
        bulk_splits = list(df_merged.attrs.get("bulk_splits", []))
        bulk_dividends = list(df_merged.attrs.get("bulk_dividends", []))
        raw_bulk_eod = df_merged.attrs.get("raw_bulk_eod")
        raw_bulk_splits = df_merged.attrs.get("raw_bulk_splits")
        raw_bulk_dividends = df_merged.attrs.get("raw_bulk_dividends")
        benchmark_prices = list(df_merged.attrs.get("benchmark_prices", []))
            
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
        daily_price_inserts = []
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
            adjusted_close = _safe_float(row.get('adjusted_close'))
            if close_price is not None:
                daily_price_inserts.append({
                    "ticker": ticker,
                    "date": dt_val,
                    "open": _safe_float(row.get("open")),
                    "high": _safe_float(row.get("high")),
                    "low": _safe_float(row.get("low")),
                    "close": close_price,
                    "adjusted_close": adjusted_close if adjusted_close is not None else close_price,
                    "volume": int(volume_num) if pd.notna(volume_num) else None,
                })
            
            # Fundamentals Fields
            name = _safe_str(row.get('name')) or _safe_str(row.get('Name')) or _safe_str(row.get('Company'))
            sector = _safe_str(row.get('Sector')) or _safe_str(row.get('sector'))
            industry = _safe_str(row.get('Industry')) or _safe_str(row.get('industry'))
            numeric_fundamental_fields = (
                "market_cap", "pe_ratio", "pb_ratio", "dividend_yield", "short_float",
                "analyst_recommendation", "target_price", "roe", "debt_to_equity",
                "fcf", "gross_margin", "sales_growth_5yr", "forward_pe", "peg_ratio",
                "ps_ratio", "price_cash", "price_fcf", "ev_ebitda", "ev_sales",
                "eps_growth_this_year", "eps_growth_next_year", "eps_growth_qoq",
                "eps_growth_ttm", "eps_growth_3yr", "eps_growth_5yr",
                "sales_growth_qoq", "sales_growth_ttm", "sales_growth_3yr", "roa",
                "roic", "current_ratio", "quick_ratio", "lt_debt_to_equity",
                "operating_margin", "net_profit_margin", "payout_ratio",
                "insider_ownership", "institutional_ownership",
            )
            legacy_aliases = {
                "market_cap": ("MarketCapitalization", "MarketCap"),
                "pe_ratio": ("PERatio", "PE", "TrailingPE"),
                "pb_ratio": ("PriceToBook", "PB", "PBRatio"),
                "dividend_yield": ("DividendYield", "Yield"),
                "roe": ("ROE",),
                "debt_to_equity": ("DebtToEquity",),
                "fcf": ("FCF",),
                "gross_margin": ("GrossMargin",),
                "sales_growth_5yr": ("SalesGrowth5yr",),
            }
            record = {
                "ticker": ticker,
                "date": dt_val,
                "name": name,
                "exchange": _safe_str(row.get("exchange")),
                "sector": sector,
                "industry": industry,
                "country": _safe_str(row.get("country")),
                "ipo_date": row.get("ipo_date") if isinstance(row.get("ipo_date"), date) else None,
                "shares_outstanding": int(value) if (value := _safe_float(row.get("shares_outstanding"))) is not None else None,
                "shares_float": int(value) if (value := _safe_float(row.get("shares_float"))) is not None else None,
                "close": close_price,
                "volume": int(volume_num) if pd.notna(volume_num) else None,
                **{field_name: None for field_name in TECHNICAL_SNAPSHOT_FIELDS},
            }
            for field_name in numeric_fundamental_fields:
                value = _safe_float(row.get(field_name))
                if value is None:
                    for alias in legacy_aliases.get(field_name, ()):
                        value = _safe_float(row.get(alias))
                        if value is not None:
                            break
                record[field_name] = value
            records_to_upsert.append(record)

        existing_price_keys = {
            (row["ticker"], row["date"])
            for row in daily_price_inserts
        }
        for benchmark_row in benchmark_prices:
            try:
                benchmark_date = datetime.strptime(
                    str(benchmark_row.get("date")),
                    "%Y-%m-%d",
                ).date()
            except (TypeError, ValueError):
                continue
            benchmark_key = ("SPY.US", benchmark_date)
            benchmark_close = _safe_float(benchmark_row.get("close"))
            if benchmark_close is None or benchmark_key in existing_price_keys:
                continue
            benchmark_adjusted_close = _safe_float(benchmark_row.get("adjusted_close"))
            benchmark_volume = benchmark_row.get("volume")
            daily_price_inserts.append({
                "ticker": "SPY.US",
                "date": benchmark_date,
                "open": _safe_float(benchmark_row.get("open")),
                "high": _safe_float(benchmark_row.get("high")),
                "low": _safe_float(benchmark_row.get("low")),
                "close": benchmark_close,
                "adjusted_close": (
                    benchmark_adjusted_close
                    if benchmark_adjusted_close is not None
                    else benchmark_close
                ),
                "volume": int(benchmark_volume) if pd.notna(benchmark_volume) else None,
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
            ticker_list = list(
                {record["ticker"] for record in records_to_upsert}
                | {record["ticker"] for record in daily_price_inserts}
            )
            
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
            # 2a. Sync the provider's adjusted OHLCV fields before technicals.
            if daily_price_inserts:
                 # Chunking update
                 logger.info("Upserting latest EOD prices to local daily_prices table...")
                 for i in range(0, len(daily_price_inserts), 1000):
                     chunk = daily_price_inserts[i:i+1000]
                     stmt_dp = insert(DailyPrice)
                     stmt_dp = stmt_dp.on_conflict_do_update(
                         index_elements=['ticker', 'date'],
                         set_={
                             "open": stmt_dp.excluded.open,
                             "high": stmt_dp.excluded.high,
                             "low": stmt_dp.excluded.low,
                             "close": stmt_dp.excluded.close,
                             "adjusted_close": stmt_dp.excluded.adjusted_close,
                             "volume": stmt_dp.excluded.volume,
                         }
                     )
                     await db.execute(stmt_dp, chunk)

            await persist_snapshot(
                db,
                "EODHD",
                "bulk_eod",
                raw_bulk_eod if raw_bulk_eod is not None else daily_price_inserts,
                as_of_date=snapshot_date,
                details={
                    "exchange": "US",
                    "universe": "SP500_RUSSELL2000",
                    "as_of_date": snapshot_date.isoformat(),
                },
            )
            await persist_snapshot(
                db,
                "EODHD",
                "bulk_splits",
                raw_bulk_splits if raw_bulk_splits is not None else bulk_splits,
                as_of_date=snapshot_date,
                details={"exchange": "US", "as_of_date": snapshot_date.isoformat()},
            )
            await persist_snapshot(
                db,
                "EODHD",
                "bulk_dividends",
                raw_bulk_dividends if raw_bulk_dividends is not None else bulk_dividends,
                as_of_date=snapshot_date,
                details={"exchange": "US", "as_of_date": snapshot_date.isoformat()},
            )
            splits_by_ticker: Dict[str, list] = {}
            dividends_by_ticker: Dict[str, list] = {}
            for item in bulk_splits:
                splits_by_ticker.setdefault(item["ticker"], []).append(item)
            for item in bulk_dividends:
                dividends_by_ticker.setdefault(item["ticker"], []).append(item)
            for action_ticker in set(splits_by_ticker) | set(dividends_by_ticker):
                await upsert_corporate_actions(
                    db,
                    action_ticker,
                    splits_by_ticker.get(action_ticker, []),
                    dividends_by_ticker.get(action_ticker, []),
                )
                     
            # 2b. Compute Local Technical Indicators
            df_technicals = await calculate_technicals_locally(db, ticker_list, as_of_date=snapshot_date)
            
            # 2c. Merge Technicals into the snapshot models
            if not df_technicals.empty:
                df_technicals = df_technicals.drop_duplicates(subset=['ticker'])
                tech_map = df_technicals.set_index('ticker').to_dict('index')
                for r in records_to_upsert:
                     t_data = tech_map.get(r['ticker'])
                     if t_data:
                         for field_name, value in t_data.items():
                             if field_name == "candlestick":
                                 r[field_name] = _safe_str(value)
                             else:
                                 r[field_name] = _safe_float(value)

            dividend_result = await db.execute(
                select(
                    CorporateAction.ticker,
                    CorporateAction.ex_date,
                    CorporateAction.cash_amount,
                ).where(
                    CorporateAction.ticker.in_(ticker_list),
                    CorporateAction.action_type == "dividend",
                    CorporateAction.ex_date <= snapshot_date,
                    CorporateAction.ex_date >= snapshot_date - timedelta(days=365 * 7),
                )
            )
            dividends_by_security: Dict[str, list] = {}
            for dividend_ticker, ex_date, cash_amount in dividend_result.all():
                dividends_by_security.setdefault(dividend_ticker, []).append((ex_date, cash_amount))
            for record in records_to_upsert:
                record.update(
                    calculate_dividend_growth(
                        dividends_by_security.get(record["ticker"], []),
                        snapshot_date,
                    )
                )

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
            if sp500_universe:
                await record_universe_membership(
                    db,
                    universe="SP500",
                    tickers=sp500_universe,
                    effective_date=snapshot_date,
                    source_run_id=run_id,
                    minimum_retained_fraction=settings.PIPELINE_MIN_UNIVERSE_COVERAGE,
                )
            if russell2000_universe:
                await record_universe_membership(
                    db,
                    universe="RUSSELL2000",
                    tickers=russell2000_universe,
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
                    mutable_columns = [
                        column.name
                        for column in StockScreenerSnapshot.__table__.columns
                        if column.name not in {"id", "ticker", "date"}
                    ]
                    stmt = stmt.on_conflict_do_update(
                        index_elements=['ticker', 'date'],
                        set_={name: getattr(stmt.excluded, name) for name in mutable_columns},
                    )
                    await db.execute(stmt)

            # The normalized rows, cumulative price-history publication and
            # screener publication become visible in one commit.
            await publish_datasets_and_finish(
                db,
                ["screener", "price_history"],
                snapshot_date,
                run_id,
                quality_report=quality_report.to_dict(),
                records_processed=len(records_to_upsert),
            )

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
    """Recompute price and dividend-derived fields after cold-start history loads."""
    from sqlalchemy import update

    async with async_session_maker() as db:
        result = await db.execute(
            select(StockScreenerSnapshot.ticker).where(StockScreenerSnapshot.date == snapshot_date)
        )
        tickers = list(result.scalars().all())
        technicals = await calculate_technicals_locally(db, tickers, as_of_date=snapshot_date)
        technical_by_ticker = (
            technicals.drop_duplicates(subset=["ticker"]).set_index("ticker").to_dict("index")
            if not technicals.empty
            else {}
        )
        dividend_result = await db.execute(
            select(
                CorporateAction.ticker,
                CorporateAction.ex_date,
                CorporateAction.cash_amount,
            ).where(
                CorporateAction.ticker.in_(tickers),
                CorporateAction.action_type == "dividend",
                CorporateAction.ex_date <= snapshot_date,
                CorporateAction.ex_date >= snapshot_date - timedelta(days=365 * 7),
            )
        )
        dividends_by_security: Dict[str, list] = {}
        for dividend_ticker, ex_date, cash_amount in dividend_result.all():
            dividends_by_security.setdefault(dividend_ticker, []).append((ex_date, cash_amount))

        updated = 0
        async with db.begin_nested():
            for ticker in tickers:
                values = calculate_dividend_growth(
                    dividends_by_security.get(ticker, []),
                    snapshot_date,
                )
                for field_name, value in technical_by_ticker.get(ticker, {}).items():
                    if not hasattr(StockScreenerSnapshot, field_name):
                        continue
                    if pd.isna(value):
                        values[field_name] = None
                    elif field_name == "candlestick":
                        values[field_name] = str(value)
                    else:
                        values[field_name] = float(value)
                await db.execute(
                    update(StockScreenerSnapshot)
                    .where(
                        StockScreenerSnapshot.ticker == ticker,
                        StockScreenerSnapshot.date == snapshot_date,
                    )
                    .values(**values)
                )
                updated += 1
        await db.commit()
        return updated
