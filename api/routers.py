# api/routers.py
import logging
from datetime import date, timedelta
from typing import Any, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import (
    AnomalyScanResponse,
    BacktestRequest,
    EarningsQualityAnalysisRequest,
    PersonalWatchlistRequest,
    ValuationScenariosRequest,
    FactorComputeRequest,
    FactorResearchRequest,
    MarketOverviewResponse,
    StockDataResponse,
)
from database import database_ready, get_db
from sqlalchemy import and_, select, func
from models import BacktestRun, DailyPrice, DataPublication, FactorValue, PipelineRun
from services.data_sync import sync_ticker_data
from services.analyzer import (
    get_analyzed_stock_data, 
    get_fundamental_valuation, 
    batch_get_factor_scores,
    filter_screener_stocks,
    get_rrg_data_for_tickers
)
from services.ai_assistant import generate_stock_report, get_cached_stock_report
from services.earnings_quality import get_earnings_quality, serialize_analysis_run
from services.filing_analysis import (
    FilingAnalysisError,
    assert_filing_analysis_configured,
    enqueue_filing_analysis,
    find_reusable_analysis,
    get_filing_analysis,
    schedule_filing_analysis,
)
from services.decision_support import (
    DEFAULT_SCENARIOS,
    calculate_ticker_valuation,
    get_decision_support,
    validate_scenarios,
)
from services.personal_workspace import (
    delete_valuation_scenarios,
    get_saved_valuation_scenarios,
    get_watchlist,
    import_watchlist_if_empty,
    replace_watchlist,
    save_valuation_scenarios,
)
from services.news_fetcher import NewsFetchError, fetch_yahoo_news
from services.anomaly_scans import (
    enqueue_manual_anomaly_scan,
    get_anomaly_scan,
    get_latest_completed_anomaly_scan,
    schedule_anomaly_scan,
    serialize_anomaly_scan,
)
from core.security import (
    limit_expensive_requests,
    require_admin_api_key,
    require_configured_admin_api_key,
)
from services.security_master import canonicalize_ticker
from services.quant.backtest import BacktestConfig, run_and_store_backtest
from services.quant.factor_engine import compute_and_store_factors
from services.quant.research import evaluate_factor
from services.freshness import assess_ticker_freshness
from services.screener_query import get_screener_metadata, query_screener
from services.sync_coordinator import ticker_sync_lock
from services.market_breadth import (
    MarketOverviewUnavailable,
    MarketOverviewUniverseUnavailable,
    get_market_overview,
)
import pandas as pd

router = APIRouter()
logger = logging.getLogger(__name__)

class BatchFactorsRequest(BaseModel):
    tickers: List[str] = Field(min_length=1, max_length=100)

class ScreenerRequest(BaseModel):
    as_of_date: Optional[date] = None
    market_cap_min: Optional[float] = None
    market_cap_max: Optional[float] = None
    pe_min: Optional[float] = None
    pe_max: Optional[float] = None
    pb_min: Optional[float] = None
    pb_max: Optional[float] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    rsi_14_min: Optional[float] = None
    rsi_14_max: Optional[float] = None
    volume_min: Optional[int] = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    dividend_yield_min: Optional[float] = None
    price_above_ma50: Optional[bool] = None
    price_below_ma50: Optional[bool] = None
    roe_min: Optional[float] = None
    debt_to_equity_max: Optional[float] = None
    fcf_min: Optional[float] = None
    gross_margin_min: Optional[float] = None
    sales_growth_5yr_min: Optional[float] = None
    
    # Sorting and Pagination
    sort_by: Optional[str] = "market_cap" # e.g., "market_cap", "pe_ratio", "volume", "rsi_14", "close"
    sort_desc: Optional[bool] = True
    limit: int = Field(50, ge=1, le=500)
    offset: int = Field(0, ge=0, le=1_000_000)


async def _load_latest_published_factor_snapshot(ticker: str, db: AsyncSession) -> Optional[dict]:
    ticker = canonicalize_ticker(ticker)
    publication_result = await db.execute(
        select(DataPublication)
        .join(PipelineRun, PipelineRun.id == DataPublication.pipeline_run_id)
        .where(
            DataPublication.dataset == "factors",
            DataPublication.status == "published",
            PipelineRun.status == "published",
        )
        .order_by(DataPublication.as_of_date.desc())
        .limit(1)
    )
    publication = publication_result.scalar_one_or_none()
    if publication is None:
        return None

    factor_result = await db.execute(
        select(FactorValue).where(
            FactorValue.ticker == ticker,
            FactorValue.as_of_date == publication.as_of_date,
            FactorValue.source_run_id == publication.pipeline_run_id,
        )
    )
    rows = factor_result.scalars().all()
    if not rows:
        return None

    rows_by_version = {}
    for row in rows:
        rows_by_version.setdefault(row.version, []).append(row)
    version = max(rows_by_version, key=lambda item: (len(rows_by_version[item]), item))
    version_rows = rows_by_version[version]
    return {
        "ticker": ticker,
        "as_of_date": publication.as_of_date.isoformat(),
        "published_at": publication.published_at.isoformat(),
        "version": version,
        "factors": {
            row.factor_name: {
                "raw_value": row.raw_value,
                "normalized_value": row.normalized_value,
                "details": row.details,
            }
            for row in version_rows
        },
    }


class ScreenerFilterClause(BaseModel):
    field: str = Field(min_length=1, max_length=64)
    operator: Literal["eq", "in", "lt", "lte", "gt", "gte", "between"]
    value: Any


class ScreenerSort(BaseModel):
    field: str = "market_cap"
    direction: Literal["asc", "desc"] = "desc"


class ScreenerQueryRequest(BaseModel):
    as_of_date: Optional[date] = None
    filters: List[ScreenerFilterClause] = Field(default_factory=list, max_length=64)
    sort: ScreenerSort = Field(default_factory=ScreenerSort)
    columns: List[str] = Field(default_factory=list, max_length=30)
    limit: int = Field(50, ge=1, le=500)
    offset: int = Field(0, ge=0, le=1_000_000)

    @field_validator("columns")
    @classmethod
    def unique_columns(cls, value: List[str]) -> List[str]:
        return list(dict.fromkeys(value))

@router.post("/api/stocks/screener", tags=["Stocks Analysis Read"])
async def read_stock_screener(request: ScreenerRequest, db: AsyncSession = Depends(get_db)):
    """
    Dynamically scan the market and filter stocks across fundamental and technical dimensions.
    """
    results = await filter_screener_stocks(request.model_dump(), db)
    return results


@router.get("/api/stocks/screener/metadata", tags=["Stocks Analysis Read"])
async def read_screener_metadata(db: AsyncSession = Depends(get_db)):
    return await get_screener_metadata(db)


@router.post("/api/stocks/screener/query", tags=["Stocks Analysis Read"])
async def read_screener_query(request: ScreenerQueryRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await query_screener(request.model_dump(exclude_unset=True), db)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@router.post("/api/stocks/batch-factors", tags=["Stocks Analysis Read"])
async def read_batch_factors(request: BatchFactorsRequest, db: AsyncSession = Depends(get_db)):
    """
    Fetch fundamental multi-factor scores for a batch of tickers with bulk database reads.
    """
    if not request.tickers:
        return []
    results = await batch_get_factor_scores(request.tickers, db)
    return results


def _scenario_dicts(request: ValuationScenariosRequest) -> list[dict[str, Any]]:
    try:
        return validate_scenarios(
            [scenario.model_dump() for scenario in request.scenarios]
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/api/stocks/{ticker}/decision-support",
    tags=["Stocks Decision Support"],
)
async def read_decision_support(
    ticker: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return deterministic decision evidence; saved assumptions are opt-in via Admin Key."""
    include_saved = False
    api_key = request.headers.get("x-api-key")
    if api_key:
        await require_admin_api_key(api_key)
        include_saved = True
    return await get_decision_support(
        ticker,
        db,
        include_saved_scenarios=include_saved,
    )


@router.get(
    "/api/stocks/{ticker}/earnings-quality",
    tags=["Stocks Decision Support"],
)
async def read_earnings_quality(
    ticker: str,
    db: AsyncSession = Depends(get_db),
):
    """Return deterministic flags and cached analyses without external work."""
    return await get_earnings_quality(ticker, db)


@router.post(
    "/api/personal/stocks/{ticker}/earnings-quality/analyses",
    tags=["Personal Workspace"],
    dependencies=[Depends(require_configured_admin_api_key)],
)
async def start_earnings_quality_analysis(
    ticker: str,
    payload: EarningsQualityAnalysisRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Queue one explicitly selected SEC period; never called by normal page reads."""
    try:
        reusable = await find_reusable_analysis(
            db,
            ticker,
            payload.period_end,
            payload.period_type,
        )
        if reusable is not None:
            response.status_code = (
                status.HTTP_200_OK
                if reusable.status == "completed"
                else status.HTTP_202_ACCEPTED
            )
            return serialize_analysis_run(reusable)

        # Only a cache miss that can create external work consumes the budget.
        assert_filing_analysis_configured()
        await limit_expensive_requests(request)
        run, created = await enqueue_filing_analysis(
            db,
            ticker,
            payload.period_end,
            payload.period_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FilingAnalysisError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if created:
        schedule_filing_analysis(run.id)
    response.status_code = (
        status.HTTP_200_OK
        if run.status == "completed"
        else status.HTTP_202_ACCEPTED
    )
    return serialize_analysis_run(run)


@router.get(
    "/api/stocks/{ticker}/earnings-quality/analyses/{analysis_id}",
    tags=["Stocks Decision Support"],
    dependencies=[Depends(require_admin_api_key)],
)
async def read_earnings_quality_analysis(
    ticker: str,
    analysis_id: int,
    db: AsyncSession = Depends(get_db),
):
    run = await get_filing_analysis(db, ticker, analysis_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Earnings-quality analysis not found")
    return serialize_analysis_run(run)


@router.post(
    "/api/stocks/{ticker}/valuation/calculate",
    tags=["Stocks Decision Support"],
)
async def calculate_stock_valuation(
    ticker: str,
    request: ValuationScenariosRequest,
    db: AsyncSession = Depends(get_db),
):
    scenarios = _scenario_dicts(request)
    return await calculate_ticker_valuation(ticker, db, scenarios)


@router.get(
    "/api/personal/stocks/{ticker}/valuation-scenarios",
    tags=["Personal Workspace"],
    dependencies=[Depends(require_admin_api_key)],
)
async def read_personal_valuation_scenarios(
    ticker: str,
    db: AsyncSession = Depends(get_db),
):
    canonical_ticker = canonicalize_ticker(ticker)
    saved = await get_saved_valuation_scenarios(db, canonical_ticker)
    return {
        "ticker": canonical_ticker,
        "is_saved": saved is not None,
        "scenarios": saved or DEFAULT_SCENARIOS,
    }


@router.put(
    "/api/personal/stocks/{ticker}/valuation-scenarios",
    tags=["Personal Workspace"],
    dependencies=[Depends(require_admin_api_key)],
)
async def put_personal_valuation_scenarios(
    ticker: str,
    request: ValuationScenariosRequest,
    db: AsyncSession = Depends(get_db),
):
    scenarios = _scenario_dicts(request)
    saved = await save_valuation_scenarios(db, ticker, scenarios)
    return {
        "ticker": canonicalize_ticker(ticker),
        "is_saved": True,
        "scenarios": saved,
    }


@router.delete(
    "/api/personal/stocks/{ticker}/valuation-scenarios",
    tags=["Personal Workspace"],
    dependencies=[Depends(require_admin_api_key)],
)
async def remove_personal_valuation_scenarios(
    ticker: str,
    db: AsyncSession = Depends(get_db),
):
    await delete_valuation_scenarios(db, ticker)
    return {
        "ticker": canonicalize_ticker(ticker),
        "is_saved": False,
        "scenarios": DEFAULT_SCENARIOS,
    }


@router.get(
    "/api/personal/watchlist",
    tags=["Personal Workspace"],
    dependencies=[Depends(require_admin_api_key)],
)
async def read_personal_watchlist(db: AsyncSession = Depends(get_db)):
    return {"tickers": await get_watchlist(db)}


@router.put(
    "/api/personal/watchlist",
    tags=["Personal Workspace"],
    dependencies=[Depends(require_admin_api_key)],
)
async def put_personal_watchlist(
    request: PersonalWatchlistRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        tickers = await replace_watchlist(db, request.tickers)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"tickers": tickers}


@router.post(
    "/api/personal/watchlist/import",
    tags=["Personal Workspace"],
    dependencies=[Depends(require_admin_api_key)],
)
async def import_personal_watchlist(
    request: PersonalWatchlistRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        tickers, imported = await import_watchlist_if_empty(db, request.tickers)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"tickers": tickers, "imported": imported}

@router.post(
    "/api/stocks/{ticker}/sync",
    tags=["Stocks Synchronization"],
    dependencies=[Depends(require_admin_api_key), Depends(limit_expensive_requests)],
)
async def sync_stock_data(ticker: str, db: AsyncSession = Depends(get_db)):
    """
    触发对指定股票的底层基础数据 (Fundamentals 和 Daily Prices) 拉取与全量数据库同步。
    """
    ticker = canonicalize_ticker(ticker)
    success = await sync_ticker_data(ticker, db)
    
    if not success:
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to synchronize data for ticker: {ticker}. See server logs for details."
        )
    return {"message": f"Successfully synchronized data for {ticker}", "ticker": ticker}

@router.get("/api/stocks/{ticker}", response_model=StockDataResponse, tags=["Stocks Analysis Read"])
async def read_stock_analysis(ticker: str, request: Request, interval: str = "1d", financial_period: str = "Yearly", db: AsyncSession = Depends(get_db)):
    """
    读取指定股票的基础 Profile 以及经过量化分析 (MA, RSI, MACD等) 后的全量历史时间序列。
    实现了 Read-Through 策略: 如果本地数据陈旧或不存在，自动触发获取。
    """
    ticker = canonicalize_ticker(ticker)
    data = await get_analyzed_stock_data(ticker, db, interval, financial_period)
    
    freshness = await assess_ticker_freshness(db, ticker)
    needs_sync = not data or freshness.needs_sync

    if needs_sync:
        async with ticker_sync_lock(ticker):
            # A concurrent request may have completed the same cold sync while
            # this request waited for the single-flight lock. End the previous
            # read transaction so SQLite can observe that concurrent commit.
            await db.rollback()
            freshness = await assess_ticker_freshness(db, ticker)
            success = True
            if freshness.needs_sync:
                # Cached reads stay unrestricted, while each external API sync
                # consumes the same per-client budget as other costly routes.
                await limit_expensive_requests(request)
                success = await sync_ticker_data(ticker, db)
            if not success:
                raise HTTPException(
                    status_code=503,
                    detail=f"Data for {ticker} is unavailable and external synchronization failed."
                )
        # Attempt to read again after synchronization
        data = await get_analyzed_stock_data(ticker, db, interval, financial_period)
        
        if not data:
             raise HTTPException(
                status_code=500, 
                detail=f"Synchronization succeeded but analytics extraction failed for {ticker}."
            )
        
    valuation = await get_fundamental_valuation(ticker, db)
    data["valuation_metrics"] = valuation
    
    return data

@router.get(
    "/api/stocks/{ticker}/report",
    tags=["AI Report Generation"],
)
async def read_ai_stock_report(
    ticker: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Generate an optional AI brief from decision-support evidence only.
    """
    ticker = canonicalize_ticker(ticker)
    include_saved = False
    api_key = request.headers.get("x-api-key")
    if api_key:
        await require_admin_api_key(api_key)
        include_saved = True

    async def static_report(content: str):
        yield content

    try:
        decision = await get_decision_support(
            ticker,
            db,
            include_saved_scenarios=include_saved,
        )
        cached = await get_cached_stock_report(ticker, decision, db)
    except Exception:
        logger.exception("Unable to build AI report evidence for %s", ticker)
        return StreamingResponse(
            static_report(
                "Error: The evidence brief is temporarily unavailable. "
                "Deterministic cockpit data remains available."
            ),
            media_type="text/event-stream",
        )

    # Cached reads are deterministic database lookups and should not consume
    # the budget reserved for calls that can reach an external model provider.
    if cached is not None:
        return StreamingResponse(static_report(cached), media_type="text/event-stream")

    await limit_expensive_requests(request)

    async def report_generator():
        try:
            async for chunk in generate_stock_report(ticker, decision, db):
                yield chunk
        except Exception:
            logger.exception("Unable to generate AI report for %s", ticker)
            yield (
                "Error: The evidence brief is temporarily unavailable. "
                "Deterministic cockpit data remains available."
            )
    
    return StreamingResponse(report_generator(), media_type="text/event-stream")

@router.get(
    "/api/stocks/{ticker}/news",
    dependencies=[Depends(limit_expensive_requests)],
)
async def get_stock_news(ticker: str):
    """
    Returns the latest news for a given stock ticker from the past 72 hours.
    """
    try:
        return await fetch_yahoo_news(canonicalize_ticker(ticker))
    except NewsFetchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

@router.get(
    "/api/market/anomalies",
    response_model=Optional[AnomalyScanResponse],
)
async def get_market_anomalies(db: AsyncSession = Depends(get_db)):
    """Return the latest successfully completed anomaly scan without external work."""
    scan = await get_latest_completed_anomaly_scan(db)
    return await serialize_anomaly_scan(db, scan) if scan else None


@router.post(
    "/api/market/anomalies/scans",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(limit_expensive_requests)],
    response_model=AnomalyScanResponse,
)
async def start_market_anomaly_scan(db: AsyncSession = Depends(get_db)):
    """Queue a single-flight anomaly scan and return immediately."""
    scan, created = await enqueue_manual_anomaly_scan(db)
    if created:
        schedule_anomaly_scan(scan.id)
    return await serialize_anomaly_scan(db, scan)


@router.get(
    "/api/market/anomalies/scans/{scan_id}",
    response_model=AnomalyScanResponse,
)
async def get_market_anomaly_scan(
    scan_id: int,
    db: AsyncSession = Depends(get_db),
):
    scan = await get_anomaly_scan(db, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Anomaly scan not found")
    return await serialize_anomaly_scan(db, scan)

@router.get("/api/v1/rrg", tags=["Stocks Analysis Read"])
async def get_rrg(
    tickers: str,
    benchmark: str = "SPY.US",
    history_days: int = Query(default=252, ge=1, le=252),
    db: AsyncSession = Depends(get_db)
):
    """
    Get Relative Rotation Graph (RRG) data for a list of tickers against a benchmark.
    `tickers` should be a comma-separated string, e.g., "AAPL,MSFT,NVDA".
    """
    if not tickers or not tickers.strip():
        raise HTTPException(status_code=400, detail="Parameter 'tickers' cannot be empty.")
        
    ticker_list = list(dict.fromkeys(
        canonicalize_ticker(t) for t in tickers.split(",") if t.strip()
    ))
    if not ticker_list:
        raise HTTPException(status_code=400, detail="No valid tickers extracted from input.")
    if len(ticker_list) > 50:
        raise HTTPException(status_code=422, detail="At most 50 tickers may be requested at once.")
        
    try:
        data = await get_rrg_data_for_tickers(
            tickers=ticker_list,
            db_session=db,
            benchmark=canonicalize_ticker(benchmark),
            history_days=history_days
        )
        return data  # FastAPI会自动序列化为JSONResponse
    except Exception:
        logger.exception("Failed to calculate RRG data")
        raise HTTPException(status_code=500, detail="Failed to calculate RRG data")


@router.get(
    "/api/v1/market-overview",
    response_model=MarketOverviewResponse,
    tags=["Market Analysis Read"],
)
async def market_overview(
    universe: Literal["SP500", "RUSSELL2000", "SP500_RUSSELL2000"] = "SP500",
    period: Literal["3m", "6m", "1y"] = "1y",
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_market_overview(db, universe, period)
    except MarketOverviewUniverseUnavailable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MarketOverviewUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/health/live", tags=["Operations"])
async def health_live():
    return {"status": "ok"}


@router.get("/health/ready", tags=["Operations"])
async def health_ready():
    if not await database_ready():
        raise HTTPException(status_code=503, detail="Database is not ready")
    return {"status": "ready"}


@router.post(
    "/api/quant/factors/compute",
    tags=["Quant Research"],
    dependencies=[Depends(require_admin_api_key), Depends(limit_expensive_requests)],
)
async def compute_factors(request: FactorComputeRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await compute_and_store_factors(db, request.as_of_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/quant/factors", tags=["Quant Research"])
async def read_factors(
    as_of_date: date,
    factor_name: str = "composite",
    version: str = "lfq-v1",
    limit: int = 500,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(FactorValue)
        .join(
            DataPublication,
            and_(
                DataPublication.dataset == "factors",
                DataPublication.status == "published",
                DataPublication.as_of_date == FactorValue.as_of_date,
                DataPublication.pipeline_run_id == FactorValue.source_run_id,
            ),
        )
        .join(
            PipelineRun,
            and_(
                PipelineRun.id == FactorValue.source_run_id,
                PipelineRun.status == "published",
            ),
        )
        .where(
            FactorValue.as_of_date == as_of_date,
            FactorValue.factor_name == factor_name,
            FactorValue.version == version,
        )
        .order_by(FactorValue.normalized_value.desc())
        .limit(min(max(limit, 1), 5000))
    )
    return [
        {
            "ticker": row.ticker,
            "as_of_date": row.as_of_date.isoformat(),
            "factor_name": row.factor_name,
            "raw_value": row.raw_value,
            "normalized_value": row.normalized_value,
            "version": row.version,
            "available_at": row.available_at.isoformat(),
            "details": row.details,
        }
        for row in result.scalars().all()
    ]


@router.get("/api/quant/coverage", tags=["Quant Research"])
async def read_quant_coverage(db: AsyncSession = Depends(get_db)):
    publication_result = await db.execute(
        select(DataPublication)
        .join(PipelineRun, PipelineRun.id == DataPublication.pipeline_run_id)
        .where(
            DataPublication.status == "published",
            PipelineRun.status == "published",
            DataPublication.dataset.in_(["screener", "price_history", "factors"]),
        )
        .order_by(DataPublication.dataset, DataPublication.as_of_date.desc())
    )
    latest_by_dataset = {}
    for publication in publication_result.scalars().all():
        if publication.dataset not in latest_by_dataset:
            latest_by_dataset[publication.dataset] = {
                "as_of_date": publication.as_of_date.isoformat(),
                "published_at": publication.published_at.isoformat(),
            }

    coverage_result = await db.execute(
        select(
            func.min(FactorValue.as_of_date),
            func.max(FactorValue.as_of_date),
            func.count(func.distinct(FactorValue.as_of_date)),
            func.count(func.distinct(FactorValue.ticker)),
        )
        .join(
            DataPublication,
            and_(
                DataPublication.dataset == "factors",
                DataPublication.status == "published",
                DataPublication.as_of_date == FactorValue.as_of_date,
                DataPublication.pipeline_run_id == FactorValue.source_run_id,
            ),
        )
        .join(
            PipelineRun,
            and_(
                PipelineRun.id == FactorValue.source_run_id,
                PipelineRun.status == "published",
            ),
        )
    )
    min_date, max_date, date_count, ticker_count = coverage_result.one()
    factor_name_result = await db.execute(
        select(FactorValue.factor_name)
        .join(
            DataPublication,
            and_(
                DataPublication.dataset == "factors",
                DataPublication.status == "published",
                DataPublication.as_of_date == FactorValue.as_of_date,
                DataPublication.pipeline_run_id == FactorValue.source_run_id,
            ),
        )
        .join(
            PipelineRun,
            and_(
                PipelineRun.id == FactorValue.source_run_id,
                PipelineRun.status == "published",
            ),
        )
        .distinct()
        .order_by(FactorValue.factor_name)
    )
    return {
        "publications": latest_by_dataset,
        "factors": {
            "min_date": min_date.isoformat() if min_date else None,
            "max_date": max_date.isoformat() if max_date else None,
            "date_count": date_count or 0,
            "ticker_count": ticker_count or 0,
            "names": list(factor_name_result.scalars().all()),
        },
    }


@router.get("/api/quant/factors/{ticker}/latest", tags=["Quant Research"])
async def read_latest_ticker_factors(ticker: str, db: AsyncSession = Depends(get_db)):
    ticker = canonicalize_ticker(ticker)
    snapshot = await _load_latest_published_factor_snapshot(ticker, db)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"No published factors are available for {ticker}")
    return snapshot


@router.post(
    "/api/quant/research",
    tags=["Quant Research"],
    dependencies=[Depends(limit_expensive_requests)],
)
async def research_factor(request: FactorResearchRequest, db: AsyncSession = Depends(get_db)):
    factor_result = await db.execute(
        select(FactorValue)
        .join(
            DataPublication,
            and_(
                DataPublication.dataset == "factors",
                DataPublication.status == "published",
                DataPublication.as_of_date == FactorValue.as_of_date,
                DataPublication.pipeline_run_id == FactorValue.source_run_id,
            ),
        )
        .join(
            PipelineRun,
            and_(
                PipelineRun.id == FactorValue.source_run_id,
                PipelineRun.status == "published",
            ),
        )
        .where(
            FactorValue.factor_name == request.factor_name,
            FactorValue.version == request.factor_version,
            FactorValue.as_of_date >= request.start_date,
            FactorValue.as_of_date <= request.end_date,
        )
    )
    factor_rows = factor_result.scalars().all()
    factor_frame = pd.DataFrame([
        {
            "ticker": row.ticker,
            "as_of_date": row.as_of_date,
            "available_at": row.available_at,
            "normalized_value": row.normalized_value,
        }
        for row in factor_rows
    ])
    tickers = sorted({row.ticker for row in factor_rows})
    price_rows = []
    price_end = request.end_date + timedelta(days=request.horizon_days * 2 + 10)
    for start in range(0, len(tickers), 500):
        result = await db.execute(
            select(DailyPrice).where(
                DailyPrice.ticker.in_(tickers[start:start + 500]),
                DailyPrice.date >= request.start_date,
                DailyPrice.date <= price_end,
            )
        )
        price_rows.extend(result.scalars().all())
    price_frame = pd.DataFrame([
        {
            "ticker": row.ticker,
            "date": row.date,
            "close": float(row.close) if row.close is not None else None,
            "adjusted_close": float(row.adjusted_close) if row.adjusted_close is not None else None,
        }
        for row in price_rows
    ])
    try:
        return evaluate_factor(factor_frame, price_frame, request.horizon_days, request.quantiles)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/api/quant/backtests",
    tags=["Quant Research"],
    dependencies=[Depends(require_admin_api_key), Depends(limit_expensive_requests)],
)
async def create_backtest(request: BacktestRequest, db: AsyncSession = Depends(get_db)):
    if request.end_date <= request.start_date:
        raise HTTPException(status_code=422, detail="end_date must be after start_date")
    config = BacktestConfig(**request.model_dump(exclude={"name"}))
    try:
        run = await run_and_store_backtest(db, config, name=request.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": run.id, "status": run.status, "metrics": run.metrics, "diagnostics": run.diagnostics}


@router.get(
    "/api/quant/backtests/{run_id}",
    tags=["Quant Research"],
    dependencies=[Depends(require_admin_api_key)],
)
async def read_backtest(run_id: int, db: AsyncSession = Depends(get_db)):
    run = await db.get(BacktestRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return {
        "id": run.id,
        "name": run.name,
        "status": run.status,
        "config": run.config,
        "metrics": run.metrics,
        "equity_curve": run.equity_curve,
        "attribution": run.attribution,
        "diagnostics": run.diagnostics,
    }


@router.get(
    "/api/operations/pipelines",
    tags=["Operations"],
    dependencies=[Depends(require_admin_api_key)],
)
async def read_pipeline_runs(limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(min(max(limit, 1), 500))
    )
    return [
        {
            "id": row.id,
            "pipeline_name": row.pipeline_name,
            "target_date": row.target_date.isoformat() if row.target_date else None,
            "status": row.status,
            "stage": row.stage,
            "records_processed": row.records_processed,
            "quality_report": row.quality_report,
            "error_message": row.error_message,
            "started_at": row.started_at.isoformat(),
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        }
        for row in result.scalars().all()
    ]
