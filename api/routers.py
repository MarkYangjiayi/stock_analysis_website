# api/routers.py
from datetime import date, timedelta
from typing import List, Optional
from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import BacktestRequest, FactorComputeRequest, FactorResearchRequest, StockDataResponse
from database import database_ready, get_db
from sqlalchemy import select, func
from models import BacktestRun, DailyPrice, FactorValue, PipelineRun
from services.data_sync import sync_ticker_data
from services.analyzer import (
    get_analyzed_stock_data, 
    get_fundamental_valuation, 
    batch_get_factor_scores,
    filter_screener_stocks,
    get_rrg_data_for_tickers
)
from services.ai_assistant import generate_stock_report
from services.news_fetcher import fetch_yahoo_news
from services.anomaly_detector import scan_and_analyze_anomalies
from core.security import limit_expensive_requests, require_admin_api_key
from services.security_master import canonicalize_ticker
from services.quant.backtest import BacktestConfig, run_and_store_backtest
from services.quant.factor_engine import compute_and_store_factors
from services.quant.research import evaluate_factor
from services.freshness import assess_ticker_freshness
from services.sync_coordinator import ticker_sync_lock
import pandas as pd

router = APIRouter()

class BatchFactorsRequest(BaseModel):
    tickers: List[str]

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
    offset: int = Field(0, ge=0)

@router.post("/api/stocks/screener", tags=["Stocks Analysis Read"])
async def read_stock_screener(request: ScreenerRequest, db: AsyncSession = Depends(get_db)):
    """
    Dynamically scan the market and filter stocks across fundamental and technical dimensions.
    """
    results = await filter_screener_stocks(request.model_dump(), db)
    return results

@router.post("/api/stocks/batch-factors", tags=["Stocks Analysis Read"])
async def read_batch_factors(request: BatchFactorsRequest, db: AsyncSession = Depends(get_db)):
    """
    Fetch fundamental multi-factor scores for a batch of tickers with bulk database reads.
    """
    if not request.tickers:
        return []
    results = await batch_get_factor_scores(request.tickers, db)
    return results

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
    dependencies=[Depends(limit_expensive_requests)],
)
async def read_ai_stock_report(ticker: str, db: AsyncSession = Depends(get_db)):
    """
    Generate an AI investment brief based on latest quantitive data points.
    Includes read-through automatic synchronization.
    """
    ticker = canonicalize_ticker(ticker)
    data = await get_analyzed_stock_data(ticker, db)
    
    freshness = await assess_ticker_freshness(db, ticker)
    if not data or freshness.needs_sync:
        async with ticker_sync_lock(ticker):
            await db.rollback()
            freshness = await assess_ticker_freshness(db, ticker)
            success = True if not freshness.needs_sync else await sync_ticker_data(ticker, db)
        if not success:
            raise HTTPException(
                status_code=503, 
                detail=f"Data for {ticker} not found and external synchronization failed."
            )
        data = await get_analyzed_stock_data(ticker, db)
        if not data:
             raise HTTPException(
                status_code=500, 
                detail=f"Synchronization succeeded but analytics extraction failed for {ticker}."
            )
        
    valuation = await get_fundamental_valuation(ticker, db)
    data["valuation_metrics"] = valuation
    
    report_generator = generate_stock_report(ticker, data)
    
    return StreamingResponse(report_generator, media_type="text/event-stream")

@router.get("/api/stocks/{ticker}/news")
async def get_stock_news(ticker: str):
    """"
    Returns the latest news for a given stock ticker from the past 72 hours.
    Empty array is returned if no news or an error occurs.
    """
    news_items = await fetch_yahoo_news(canonicalize_ticker(ticker))
    return news_items

@router.get("/api/market/anomalies", dependencies=[Depends(limit_expensive_requests)])
async def get_market_anomalies(db: AsyncSession = Depends(get_db)):
    """
    Returns a list of anomalous stock price movements with AI-generated attribution reports.
    Executes synchronously in the request path for MVP.
    """
    anomalies = await scan_and_analyze_anomalies(db, limit_count=10)
    return anomalies

@router.get("/api/v1/rrg", tags=["Stocks Analysis Read"])
async def get_rrg(
    tickers: str,
    benchmark: str = "SPY.US",
    history_days: int = 252,
    db: AsyncSession = Depends(get_db)
):
    """
    Get Relative Rotation Graph (RRG) data for a list of tickers against a benchmark.
    `tickers` should be a comma-separated string, e.g., "AAPL,MSFT,NVDA".
    """
    if not tickers or not tickers.strip():
        raise HTTPException(status_code=400, detail="Parameter 'tickers' cannot be empty.")
        
    ticker_list = [canonicalize_ticker(t) for t in tickers.split(",") if t.strip()]
    if not ticker_list:
        raise HTTPException(status_code=400, detail="No valid tickers extracted from input.")
        
    try:
        data = await get_rrg_data_for_tickers(
            tickers=ticker_list,
            db_session=db,
            benchmark=canonicalize_ticker(benchmark),
            history_days=history_days
        )
        return data  # FastAPI会自动序列化为JSONResponse
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to calculate RRG data: {str(e)}")


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


@router.post(
    "/api/quant/research",
    tags=["Quant Research"],
    dependencies=[Depends(limit_expensive_requests)],
)
async def research_factor(request: FactorResearchRequest, db: AsyncSession = Depends(get_db)):
    factor_result = await db.execute(
        select(FactorValue).where(
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


@router.get("/api/quant/backtests/{run_id}", tags=["Quant Research"])
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
