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
    latest_published_date,
    publish_datasets_and_finish,
    update_pipeline_run,
)
from services.corporate_actions import upsert_corporate_actions
from services.security_master import bulk_upsert_securities
from services.universe import (
    LIVE_UNIVERSE_SOURCE,
    SCREENER_INDEXES,
    SCREENER_INDEX_LABELS,
    SCREENER_INDEX_UNIVERSES,
    SCREENER_UNIVERSE,
    record_universe_membership,
)
from services.raw_store import persist_snapshot
from services.data_sync import _upsert_financials
from services.history_backfill import (
    backfill_dividend_history_once,
    backfill_price_history,
    has_unpublished_market_session_gap,
)
from services.screener_metrics import (
    calculate_dividend_growth,
    calculate_price_metrics,
    extract_fundamental_metrics,
    normalize_peg_ratio,
    validated_adjusted_returns,
)
from services.screener_normalization import (
    is_non_primary_exchange,
    normalize_nonnegative,
    normalize_positive,
    normalize_public_screener_values,
)
from core.config import settings
from core.time_utils import utc_now

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

TECHNICAL_SNAPSHOT_FIELDS = (
    "technical_quality",
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


def _validate_index_components(
    universe: str,
    tickers: list[str],
    minimum_size: int,
) -> None:
    component_count = len({ticker.upper() for ticker in tickers})
    if component_count < minimum_size:
        raise ValueError(
            f"{universe} component universe is too small: "
            f"{component_count} < {minimum_size}"
        )


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
                    "Exchange": gen.get("Exchange"),
                    "Sector": gen.get("Sector"),
                    "Industry": gen.get("Industry"),
                    "Description": gen.get("Description"),
                    "CurrencyCode": gen.get("CurrencyCode"),
                    **metrics,
                })

    # Run tasks with progress logging
    symbols = sorted(tickers)
    total_tasks = len(symbols)
    # Fundamental payloads are large. Bound the retained raw batch so a full
    # Russell 3000 run cannot grow into multi-gigabyte process memory.
    chunk_size = 100
    for i in range(0, total_tasks, chunk_size):
        await asyncio.gather(*(fetch_single(ticker) for ticker in symbols[i:i+chunk_size]))
        if on_chunk and raw_fundamentals:
            await on_chunk(dict(raw_fundamentals))
        raw_fundamentals.clear()
        logger.info(f"Fetched fundamentals: {min(i+chunk_size, total_tasks)} / {total_tasks}")

    return results


def _ticker_code(ticker: str) -> str:
    """Normalize a provider ticker to its exchange-independent symbol code."""
    return str(ticker).split(".", 1)[0].strip().upper()


def _extract_delisted_codes(rows: Any) -> set[str]:
    """Extract exchange-independent codes from EODHD's symbol-list response."""
    if not isinstance(rows, list):
        raise ValueError("Failed to retrieve the EODHD delisted symbol list.")
    return {
        str(row.get("Code") or row.get("code") or "").strip().upper()
        for row in rows
        if isinstance(row, dict) and (row.get("Code") or row.get("code"))
    }


def _filter_delisted_components(
    tickers: list[str],
    delisted_codes: set[str],
) -> list[str]:
    return [ticker for ticker in tickers if _ticker_code(ticker) not in delisted_codes]


def _materialize_screener_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Detach large lineage attrs before pandas finalizes per-row objects."""
    frame.attrs.clear()
    return frame.drop_duplicates(subset=["ticker"]).to_dict("records")


async def fetch_and_merge_bulk_data(
    target_date: str = None,
    target_tickers: set = None,
    fundamental_chunk_handler: Optional[
        Callable[[Dict[str, dict], date], Awaitable[None]]
    ] = None,
) -> pd.DataFrame:
    """
    1. Fetch Index Constituents for S&P 500, Russell 1000 and Russell 2000.
    2. Concurrently fetch bulk EOD closing prices for all US stocks.
    3. Filter bulk prices to the deduplicated Russell 3000 target universe.
    4. Concurrently fetch detailed fundamentals for the target constituents INDIVIDUALLY to save costs.
    5. Merge and return.
    """
    logger.info("Fetching target index universes (S&P 500, Russell 1000 and Russell 2000)...")

    async with eodhd_client.create_http_client() as client:
        index_tickers: dict[str, list[str]] = {
            universe: [] for universe in SCREENER_INDEX_UNIVERSES
        }
        known_exits_by_index: dict[str, set[str]] = {
            universe: set() for universe in SCREENER_INDEX_UNIVERSES
        }
        delisted_codes: set[str] = set()
        known_exits: set[str] = set()
        loaded_index_components = target_tickers is None
        if loaded_index_components:
            component_rows = await asyncio.gather(*(
                eodhd_client.get_index_components(
                    SCREENER_INDEXES[universe],
                    client=client,
                )
                for universe in SCREENER_INDEX_UNIVERSES
            ))
            index_tickers = dict(zip(SCREENER_INDEX_UNIVERSES, component_rows))
            minimum_sizes = {
                "SP500": settings.PIPELINE_MIN_SP500_SIZE,
                "RUSSELL1000": settings.PIPELINE_MIN_RUSSELL1000_SIZE,
                "RUSSELL2000": settings.PIPELINE_MIN_RUSSELL2000_SIZE,
            }
            for universe, tickers in index_tickers.items():
                _validate_index_components(
                    SCREENER_INDEX_LABELS[universe],
                    tickers,
                    minimum_sizes[universe],
                )
            delisted_rows = await eodhd_client.get_exchange_symbol_list(
                exchange="US",
                delisted=True,
                instrument_type="common_stock",
                client=client,
            )
            delisted_codes = _extract_delisted_codes(delisted_rows)
            target_tickers = set().union(*(set(tickers) for tickers in index_tickers.values()))
        target_tickers = {ticker.upper() for ticker in target_tickers}
        logger.info(f"Total unique target tickers from the Russell 3000 universe: {len(target_tickers)}")

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

        if loaded_index_components:
            # A code can be reused by a new active listing. Treat a component as
            # stale only when the code is both delisted and absent from today's
            # exchange-wide price batch.
            priced_codes = {
                str(code).strip().upper()
                for code in df_eod["code"].dropna()
                if str(code).strip()
            }
            inactive_delisted_codes = delisted_codes - priced_codes
            for universe in SCREENER_INDEX_UNIVERSES:
                original_tickers = set(index_tickers[universe])
                index_tickers[universe] = _filter_delisted_components(
                    index_tickers[universe],
                    inactive_delisted_codes,
                )
                known_exits_by_index[universe] = (
                    original_tickers - set(index_tickers[universe])
                )
            known_exits = set().union(*known_exits_by_index.values())
            minimum_sizes = {
                "SP500": settings.PIPELINE_MIN_SP500_SIZE,
                "RUSSELL1000": settings.PIPELINE_MIN_RUSSELL1000_SIZE,
                "RUSSELL2000": settings.PIPELINE_MIN_RUSSELL2000_SIZE,
            }
            for universe, tickers in index_tickers.items():
                _validate_index_components(
                    f"{SCREENER_INDEX_LABELS[universe]} after delisted-symbol filter",
                    tickers,
                    minimum_sizes[universe],
                )
            target_tickers = set().union(*(set(tickers) for tickers in index_tickers.values()))
            logger.info(
                "Excluded %s delisted index components absent from the current "
                "price batch; %s active candidates remain.",
                len(known_exits),
                len(target_tickers),
            )

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
    if loaded_index_components and not df_merged.empty:
        exchange_columns = [
            column
            for column in (
                "Exchange",
                "exchange_y",
                "Exchange_y",
                "exchange",
                "exchange_x",
            )
            if column in df_merged.columns
        ]

        def provider_exchange(row: pd.Series) -> Optional[str]:
            for column in exchange_columns:
                value = row.get(column)
                if pd.notna(value) and str(value).strip():
                    return str(value).strip()
            return None

        non_primary_tickers = set()
        for _, row in df_merged.iterrows():
            exchange = provider_exchange(row)
            if exchange is None or is_non_primary_exchange(exchange):
                non_primary_tickers.add(str(row["ticker"]).upper())
        if non_primary_tickers:
            for universe in SCREENER_INDEX_UNIVERSES:
                original_tickers = set(index_tickers[universe])
                index_tickers[universe] = [
                    ticker for ticker in index_tickers[universe]
                    if ticker.upper() not in non_primary_tickers
                ]
                removed_tickers = original_tickers - set(index_tickers[universe])
                known_exits_by_index[universe].update(removed_tickers)
            known_exits.update(non_primary_tickers)
            target_tickers = set().union(*(set(tickers) for tickers in index_tickers.values()))
            df_merged = df_merged[
                ~df_merged["ticker"].str.upper().isin(non_primary_tickers)
            ].copy()
            priced_tickers = set(df_merged["ticker"])
            universe_coverage = _validate_universe_coverage(
                target_tickers,
                priced_tickers,
            )
            minimum_sizes = {
                "SP500": settings.PIPELINE_MIN_SP500_SIZE,
                "RUSSELL1000": settings.PIPELINE_MIN_RUSSELL1000_SIZE,
                "RUSSELL2000": settings.PIPELINE_MIN_RUSSELL2000_SIZE,
            }
            for universe, tickers in index_tickers.items():
                _validate_index_components(
                    f"{SCREENER_INDEX_LABELS[universe]} after primary-listing filter",
                    tickers,
                    minimum_sizes[universe],
                )
            logger.warning(
                "Excluded %s non-primary or unknown-venue listings from the live "
                "index universe: %s",
                len(non_primary_tickers),
                ", ".join(sorted(non_primary_tickers)),
            )
    df_merged.attrs["target_tickers"] = sorted(target_tickers)
    for universe in SCREENER_INDEX_UNIVERSES:
        df_merged.attrs[f"{universe.lower()}_tickers"] = sorted(
            {ticker.upper() for ticker in index_tickers[universe]}
        )
    df_merged.attrs["russell3000_tickers"] = sorted(
        set(df_merged.attrs["russell1000_tickers"])
        | set(df_merged.attrs["russell2000_tickers"])
    )
    df_merged.attrs["known_exits"] = sorted({ticker.upper() for ticker in known_exits})
    for universe in SCREENER_INDEX_UNIVERSES:
        df_merged.attrs[f"{universe.lower()}_known_exits"] = sorted(
            {ticker.upper() for ticker in known_exits_by_index[universe]}
        )
    df_merged.attrs["russell3000_known_exits"] = sorted(
        {ticker.upper() for ticker in known_exits}
    )
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


async def _upsert_ticker_profiles(
    db: AsyncSession,
    records: list[dict[str, Any]],
) -> None:
    """Persist descriptive profile fields without erasing richer existing data."""
    ticker_profiles = [
        {
            "ticker": record["ticker"],
            "name": record.get("name"),
            "exchange": record.get("exchange"),
            "sector": record.get("sector"),
            "industry": record.get("industry"),
            "description": record.get("description"),
            "currency": record.get("currency") or "USD",
            "last_updated": utc_now(),
        }
        for record in records
        if record.get("ticker")
    ]
    for i in range(0, len(ticker_profiles), 500):
        profile_stmt = insert(Ticker).values(ticker_profiles[i:i + 500])
        profile_stmt = profile_stmt.on_conflict_do_update(
            index_elements=["ticker"],
            set_={
                "name": func.coalesce(profile_stmt.excluded.name, Ticker.name),
                "exchange": func.coalesce(profile_stmt.excluded.exchange, Ticker.exchange),
                "sector": func.coalesce(profile_stmt.excluded.sector, Ticker.sector),
                "industry": func.coalesce(profile_stmt.excluded.industry, Ticker.industry),
                "description": func.coalesce(
                    profile_stmt.excluded.description,
                    Ticker.description,
                ),
                "currency": func.coalesce(profile_stmt.excluded.currency, Ticker.currency),
                "last_updated": profile_stmt.excluded.last_updated,
            },
        )
        await db.execute(profile_stmt)


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
        )
        benchmark_returns = validated_adjusted_returns(pd.Series(
            benchmark["adjusted_close"].values,
            index=pd.to_datetime(benchmark["date"]),
        ))
        if benchmark_returns is None:
            logger.warning(
                "SPY adjusted-price history failed return validation; beta will "
                "be unavailable for the %s screener snapshot.",
                benchmark_as_of,
            )

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
        previous_screener_date = await latest_published_date("screener")
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
        russell1000_universe = set(df_merged.attrs.get("russell1000_tickers", []))
        russell2000_universe = set(df_merged.attrs.get("russell2000_tickers", []))
        known_exits = set(df_merged.attrs.get("known_exits", []))
        sp500_known_exits = set(df_merged.attrs.get("sp500_known_exits", []))
        russell1000_known_exits = set(
            df_merged.attrs.get("russell1000_known_exits", [])
        )
        russell2000_known_exits = set(
            df_merged.attrs.get("russell2000_known_exits", [])
        )
        bulk_splits = list(df_merged.attrs.get("bulk_splits", []))
        bulk_dividends = list(df_merged.attrs.get("bulk_dividends", []))
        raw_bulk_eod = df_merged.attrs.get("raw_bulk_eod")
        raw_bulk_splits = df_merged.attrs.get("raw_bulk_splits")
        raw_bulk_dividends = df_merged.attrs.get("raw_bulk_dividends")
        benchmark_prices = list(df_merged.attrs.get("benchmark_prices", []))
        # Pandas propagates DataFrame attrs via deepcopy when materializing rows.
        # The lineage attrs above contain the full exchange-wide raw payloads, so
        # detach them before deduplication and row iteration.
        merged_rows = _materialize_screener_rows(df_merged)

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
        profile_records: dict[str, dict[str, Any]] = {}
        daily_price_inserts = []
        invalid_adjusted_close_tickers: set[str] = set()
        for row in merged_rows:
            # Safely unpack row
            ticker = row.get('ticker')
            
            # EOD Fields
            date_val = row.get('date')
            try:
                dt_val = datetime.strptime(str(date_val), '%Y-%m-%d').date()
            except:
                continue
                
            close_price = normalize_positive(row.get('close'))
            volume_value = normalize_nonnegative(row.get('volume'))
            volume_num = int(volume_value) if volume_value is not None else None
            raw_adjusted_close = _safe_float(row.get('adjusted_close'))
            adjusted_close = normalize_positive(raw_adjusted_close)
            if raw_adjusted_close is not None and raw_adjusted_close <= 0:
                invalid_adjusted_close_tickers.add(ticker)
            if close_price is not None:
                daily_price_inserts.append({
                    "ticker": ticker,
                    "date": dt_val,
                    "open": normalize_positive(row.get("open")),
                    "high": normalize_positive(row.get("high")),
                    "low": normalize_positive(row.get("low")),
                    "close": close_price,
                    "adjusted_close": adjusted_close,
                    "volume": volume_num,
                })
            
            # Fundamentals Fields
            name = _safe_str(row.get('name')) or _safe_str(row.get('Name')) or _safe_str(row.get('Company'))
            exchange = _safe_str(row.get("exchange")) or _safe_str(row.get("Exchange"))
            sector = _safe_str(row.get('Sector')) or _safe_str(row.get('sector'))
            industry = _safe_str(row.get('Industry')) or _safe_str(row.get('industry'))
            description = _safe_str(row.get("description")) or _safe_str(row.get("Description"))
            currency = _safe_str(row.get("currency")) or _safe_str(row.get("CurrencyCode")) or "USD"
            profile_records[ticker] = {
                "ticker": ticker,
                "name": name,
                "exchange": exchange,
                "sector": sector,
                "industry": industry,
                "description": description,
                "currency": currency,
            }
            numeric_fundamental_fields = (
                "market_cap", "pe_ratio", "pb_ratio", "dividend_yield", "short_float",
                "analyst_recommendation", "target_price", "roe", "debt_to_equity",
                "fcf", "gross_margin", "sales_growth_5yr", "forward_pe", "peg_ratio_raw",
                "peg_ratio",
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
                "exchange": exchange,
                "sector": sector,
                "industry": industry,
                "country": _safe_str(row.get("country")),
                "ipo_date": row.get("ipo_date") if isinstance(row.get("ipo_date"), date) else None,
                "shares_outstanding": int(value) if (value := _safe_float(row.get("shares_outstanding"))) is not None else None,
                "shares_float": int(value) if (value := _safe_float(row.get("shares_float"))) is not None else None,
                "close": close_price,
                "volume": volume_num,
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
            # Older/imported rows may only contain peg_ratio. Always retain that
            # provider value while keeping the public screening value positive-only.
            peg_ratio_raw = record.get("peg_ratio_raw")
            if peg_ratio_raw is None:
                peg_ratio_raw = _safe_float(row.get("peg_ratio"))
            record["peg_ratio_raw"] = peg_ratio_raw
            record["peg_ratio"] = normalize_peg_ratio(peg_ratio_raw)
            normalize_public_screener_values(record)
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
            benchmark_close = normalize_positive(benchmark_row.get("close"))
            if benchmark_close is None or benchmark_key in existing_price_keys:
                continue
            benchmark_adjusted_close = normalize_positive(
                benchmark_row.get("adjusted_close")
            )
            benchmark_volume = normalize_nonnegative(benchmark_row.get("volume"))
            daily_price_inserts.append({
                "ticker": "SPY.US",
                "date": benchmark_date,
                "open": normalize_positive(benchmark_row.get("open")),
                "high": normalize_positive(benchmark_row.get("high")),
                "low": normalize_positive(benchmark_row.get("low")),
                "close": benchmark_close,
                "adjusted_close": benchmark_adjusted_close,
                "volume": int(benchmark_volume) if benchmark_volume is not None else None,
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

        await update_pipeline_run(run_id, "backfilling_dividend_history")
        publishable_tickers = {record["ticker"] for record in records_to_upsert}
        await backfill_dividend_history_once(
            publishable_tickers,
            snapshot_date,
            required_through_date=(
                snapshot_date
                if previous_screener_date is not None
                and has_unpublished_market_session_gap(
                    previous_screener_date,
                    snapshot_date,
                )
                else None
            ),
        )
        await update_pipeline_run(run_id, "backfilling_price_history")
        await backfill_price_history(
            publishable_tickers | {"SPY.US"},
            target_date=snapshot_date,
            include_corporate_actions=False,
            publish_dataset=False,
        )

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

            await _upsert_ticker_profiles(db, list(profile_records.values()))

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
                    "universe": SCREENER_UNIVERSE,
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
                            if field_name in {"candlestick", "technical_quality"}:
                                r[field_name] = _safe_str(value)
                            else:
                                r[field_name] = _safe_float(value)
            for record in records_to_upsert:
                if record["ticker"] not in invalid_adjusted_close_tickers:
                    continue
                for field_name in TECHNICAL_SNAPSHOT_FIELDS:
                    record[field_name] = None
                record["technical_quality"] = "invalid_adjustment_factor"

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
                universe=SCREENER_UNIVERSE,
                tickers=target_universe,
                effective_date=snapshot_date,
                source_run_id=run_id,
                minimum_retained_fraction=settings.PIPELINE_MIN_UNIVERSE_COVERAGE,
                known_exits=known_exits,
                source=LIVE_UNIVERSE_SOURCE,
            )
            # Preserve the provider's live index sets for Screener filters under
            # a separate source. Market breadth and historical backtests select
            # only HISTORICAL_UNIVERSE_SOURCE, so these observations can never
            # become a point-in-time fallback.
            if sp500_universe:
                await record_universe_membership(
                    db,
                    universe="SP500",
                    tickers=sp500_universe,
                    effective_date=snapshot_date,
                    source_run_id=run_id,
                    minimum_retained_fraction=settings.PIPELINE_MIN_UNIVERSE_COVERAGE,
                    known_exits=sp500_known_exits,
                    source=LIVE_UNIVERSE_SOURCE,
                )
            if russell1000_universe:
                await record_universe_membership(
                    db,
                    universe="RUSSELL1000",
                    tickers=russell1000_universe,
                    effective_date=snapshot_date,
                    source_run_id=run_id,
                    minimum_retained_fraction=settings.PIPELINE_MIN_UNIVERSE_COVERAGE,
                    known_exits=russell1000_known_exits,
                    source=LIVE_UNIVERSE_SOURCE,
                )
            if russell2000_universe:
                await record_universe_membership(
                    db,
                    universe="RUSSELL2000",
                    tickers=russell2000_universe,
                    effective_date=snapshot_date,
                    source_run_id=run_id,
                    minimum_retained_fraction=settings.PIPELINE_MIN_UNIVERSE_COVERAGE,
                    known_exits=russell2000_known_exits,
                    source=LIVE_UNIVERSE_SOURCE,
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
            select(
                StockScreenerSnapshot.ticker,
                StockScreenerSnapshot.technical_quality,
            ).where(StockScreenerSnapshot.date == snapshot_date)
        )
        existing_quality = dict(result.all())
        tickers = list(existing_quality)
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
                if existing_quality.get(ticker) == "invalid_adjustment_factor":
                    values.update({field_name: None for field_name in TECHNICAL_SNAPSHOT_FIELDS})
                    values["technical_quality"] = "invalid_adjustment_factor"
                else:
                    for field_name, value in technical_by_ticker.get(ticker, {}).items():
                        if not hasattr(StockScreenerSnapshot, field_name):
                            continue
                        if pd.isna(value):
                            values[field_name] = None
                        elif field_name in {"candlestick", "technical_quality"}:
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
