from typing import List
from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services.data_sync import sync_ticker_data
from services.analyzer import get_analyzed_stock_data, get_fundamental_valuation, batch_get_factor_scores
from services.ai_assistant import generate_stock_report

router = APIRouter()

class BatchFactorsRequest(BaseModel):
    tickers: List[str]

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
async def read_stock_analysis(ticker: str, db: AsyncSession = Depends(get_db)):
    """
    读取指定股票的基础 Profile 以及经过量化分析 (MA, RSI, MACD等) 后的最近 300 天日 K 线数据。
    实现了 Read-Through 策略: 如果本地数据陈旧或不存在，自动触发获取。
    """
    ticker = ticker.upper()
    data = await get_analyzed_stock_data(ticker, db)
    
    if not data:
        # Cache Miss: Trigger cold start data synchronization
        success = await sync_ticker_data(ticker, db)
        if not success:
            raise HTTPException(
                status_code=503, 
                detail=f"Data for {ticker} not found and external synchronization failed. Please try again later."
            )
        # Attempt to read again after synchronization
        data = await get_analyzed_stock_data(ticker, db)
        
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
