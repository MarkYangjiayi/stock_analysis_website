from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services.data_sync import sync_ticker_data
from services.analyzer import get_analyzed_stock_data

router = APIRouter()

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
    """
    ticker = ticker.upper()
    data = await get_analyzed_stock_data(ticker, db)
    
    if not data:
        raise HTTPException(
            status_code=404, 
            detail=f"No data found for ticker: {ticker}. Please ensure you have synchronized it first."
        )
    
    return data
