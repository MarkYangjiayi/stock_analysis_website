import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from models import DailyPrice, Ticker
from decimal import Decimal
import asyncio

from services.news_fetcher import fetch_yahoo_news
from services.ai_assistant import generate_anomaly_attribution

logger = logging.getLogger(__name__)

async def scan_and_analyze_anomalies(db: AsyncSession, limit_count: int = 5):
    """
    Scans the database for recent anomalistic price movements (change >= 2.0%).
    Fetches news, invokes LLM for attribution, and returns a list of anomaly reports.
    """
    try:
        # Step 1: Find the most recent date in the DailyPrice table
        max_date_result = await db.execute(select(func.max(DailyPrice.date)))
        latest_date = max_date_result.scalar()
        
        if not latest_date:
            logger.warning("No daily price data found for anomaly detection.")
            return []
            
        # Step 2: Query for stocks with abs((close - open)/open) >= 0.02 on the latest date
        # Restrict to `limit_count` for MVP speed considerations
        query = (
            select(DailyPrice, Ticker)
            .join(Ticker, DailyPrice.ticker == Ticker.ticker)
            .where(DailyPrice.date == latest_date)
            .where(DailyPrice.open != None)
            .where(DailyPrice.open > 0)
            .where(DailyPrice.close != None)
            .where(func.abs((DailyPrice.close - DailyPrice.open) / DailyPrice.open) >= 0.02)
            .order_by(func.abs((DailyPrice.close - DailyPrice.open) / DailyPrice.open).desc())
            .limit(limit_count)
        )
        
        result = await db.execute(query)
        rows = result.all()
        
        anomalies = []
        
        # Step 3: Iterate through found anomalies and fetch insights
        for dp, t in rows:
            ticker = t.ticker
            company_name = t.name or ticker
            open_p = float(dp.open)
            close_p = float(dp.close)
            
            price_change = ((close_p - open_p) / open_p) * 100
            price_change_rounded = round(price_change, 2)
            
            logger.info(f"Anomaly detected: {ticker} changed by {price_change_rounded}%")
            
            # Fetch News
            # Run blocking request in executor or just sync fetch if it's quick
            # fetch_yahoo_news is synchronous requests, so we should run it in an executor in production
            # For MVP, we wrap it
            news_items = await asyncio.to_thread(fetch_yahoo_news, ticker)
            
            top_news_links = [item['link'] for item in news_items[:3]]
            
            # Prepare summary list for LLM
            news_summaries = []
            for item in news_items:
                title = item.get('title', '')
                summ = item.get('summary', '')
                news_summaries.append(f"Title: {title}\nSummary: {summ}")
                
            # Invoke LLM Attribution
            ai_analysis = await generate_anomaly_attribution(
                ticker=ticker,
                price_change=price_change_rounded,
                news_list=news_summaries
            )
            
            anomalies.append({
                "ticker": ticker,
                "company_name": company_name,
                "date": str(latest_date),
                "price_change": price_change_rounded,
                "ai_analysis": ai_analysis,
                "top_news_links": top_news_links
            })
            
        return anomalies
        
    except Exception as e:
        logger.error(f"Error during anomaly detection scan: {e}")
        return []
