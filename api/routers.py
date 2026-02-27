from typing import List, Optional
from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
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

router = APIRouter()

class BatchFactorsRequest(BaseModel):
    tickers: List[str]

class ScreenerRequest(BaseModel):
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
    Fetch fundamental multi-factor scores for a batch of tickers concurrently.
    """
    if not request.tickers:
        return []
    results = await batch_get_factor_scores(request.tickers, db)
    return results

@router.post("/api/stocks/{ticker}/sync", tags=["Stocks Synchronization"])
async def sync_stock_data(ticker: str, db: AsyncSession = Depends(get_db)):
    """
    触发对指定股票的底层基础数据 (Fundamentals 和 Daily Prices) 拉取与全量数据库同步。
    """
    ticker = ticker.upper()
    success = await sync_ticker_data(ticker, db)
    
    if not success:
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to synchronize data for ticker: {ticker}. See server logs for details."
        )
    return {"message": f"Successfully synchronized data for {ticker}", "ticker": ticker}

@router.get("/api/stocks/{ticker}", tags=["Stocks Analysis Read"])
async def read_stock_analysis(ticker: str, interval: str = "1d", db: AsyncSession = Depends(get_db)):
    """
    读取指定股票的基础 Profile 以及经过量化分析 (MA, RSI, MACD等) 后的全量历史时间序列。
    实现了 Read-Through 策略: 如果本地数据陈旧或不存在，自动触发获取。
    """
    ticker = ticker.upper()
    data = await get_analyzed_stock_data(ticker, db, interval)
    
    # Check if data exists AND if the profile is fully hydrated (sector is populated).
    # The Screener daily job inserts bare-bones Ticker records (just the ticker string), 
    # so we must trigger a sync if the profile is incomplete.
    needs_sync = False
    if not data:
        needs_sync = True
    elif not data.get("profile", {}).get("sector"):
        needs_sync = True

    if needs_sync:
        # Cache Miss or Incomplete Ticker: Trigger cold start data synchronization
        success = await sync_ticker_data(ticker, db)
        if not success:
            raise HTTPException(
                status_code=503, 
                detail=f"Data for {ticker} not found and external synchronization failed. Please try again later."
            )
        # Attempt to read again after synchronization
        data = await get_analyzed_stock_data(ticker, db, interval)
        
        if not data:
             raise HTTPException(
                status_code=500, 
                detail=f"Synchronization succeeded but analytics extraction failed for {ticker}."
            )
        
    valuation = await get_fundamental_valuation(ticker, db)
    data["valuation_metrics"] = valuation
    
    return data

@router.get("/api/stocks/{ticker}/report", tags=["AI Report Generation"])
async def read_ai_stock_report(ticker: str, db: AsyncSession = Depends(get_db)):
    """
    Generate an AI investment brief based on latest quantitive data points.
    Includes read-through automatic synchronization.
    """
    ticker = ticker.upper()
    data = await get_analyzed_stock_data(ticker, db)
    
    if not data:
        success = await sync_ticker_data(ticker, db)
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
    news_items = fetch_yahoo_news(ticker)
    return news_items

@router.get("/api/market/anomalies")
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
    benchmark: str = "SPY",
    tail_length: int = 10,
    db: AsyncSession = Depends(get_db)
):
    """
    Get Relative Rotation Graph (RRG) data for a list of tickers against a benchmark.
    `tickers` should be a comma-separated string, e.g., "AAPL,MSFT,NVDA".
    """
    if not tickers or not tickers.strip():
        raise HTTPException(status_code=400, detail="Parameter 'tickers' cannot be empty.")
        
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not ticker_list:
        raise HTTPException(status_code=400, detail="No valid tickers extracted from input.")
        
    try:
        data = await get_rrg_data_for_tickers(
            tickers=ticker_list,
            db_session=db,
            benchmark=benchmark,
            tail_length=tail_length
        )
        return data  # FastAPI会自动序列化为JSONResponse
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to calculate RRG data: {str(e)}")

