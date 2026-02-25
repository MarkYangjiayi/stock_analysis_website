import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from models import StockScreenerSnapshot, Ticker
import asyncio
from typing import List, Dict, Any

from services.news_fetcher import fetch_yahoo_news
from services.ai_assistant import generate_anomaly_attribution
from services.eodhd_client import get_bulk_realtime_prices

logger = logging.getLogger(__name__)

async def scan_and_analyze_anomalies(db: AsyncSession, limit_count: int = 5):
    """
    Scans for intraday anomalies using real-time API.
    1. Grabs top 100 universe from StockScreenerSnapshot.
    2. Fetches real-time prices (batched).
    3. Filters abs(change_p) >= 2.0% and sorts.
    4. Fetches news and invokes LLM attribution for top `limit_count`.
    """
    try:
        # Step 1: Find the target universe (e.g. Top 100 stocks by market cap in the most recent snapshot)
        max_date_result = await db.execute(select(func.max(StockScreenerSnapshot.date)))
        latest_date = max_date_result.scalar()
        
        if not latest_date:
            logger.warning("No screener snapshot found for anomaly detection universe.")
            return []
            
        universe_query = (
            select(StockScreenerSnapshot.ticker, StockScreenerSnapshot.name)
            .where(StockScreenerSnapshot.date == latest_date)
            .order_by(StockScreenerSnapshot.market_cap.desc().nullslast())
            .limit(500)
        )
        universe_result = await db.execute(universe_query)
        universe = universe_result.all()
        logger.info(f"Anomaly scan: target universe length = {len(universe)}")
        
        if not universe:
            logger.warning("Universe is empty.")
            return []
            
        ticker_to_name = {row.ticker: (row.name or row.ticker) for row in universe}
        
        # Step 2: Fetch real-time prices for all US stocks in one API call
        all_realtime_data = await get_bulk_realtime_prices("US")
        logger.info(f"Anomaly scan: fetched {len(all_realtime_data) if all_realtime_data else 0} realtime quotes.")
        
        if not all_realtime_data:
            logger.warning("Failed to fetch bulk real-time prices.")
            return []
            
        # Step 3: Compute and filter real-time anomalies
        anomalies_candidates = []
        for quote in all_realtime_data:
            code = quote.get("code")
            
            # EODHD Bulk Realtime returns code without .US sometimes, but usually with it if we request US. 
            # If it returns without .US, we should append it.
            if code and not code.endswith(".US"):
                code += ".US"
                
            if not code or code not in ticker_to_name:
                continue
                
            change_p = quote.get("change_p")
            
            # Use 'change_p' if present. Wait, it could be None or string
            try:
                change_pct: float = float(change_p)
                if abs(change_pct) >= 2.0:
                    anomalies_candidates.append({
                        "ticker": code,
                        "company_name": ticker_to_name[code],
                        "price_change": change_pct
                    })
            except (ValueError, TypeError):
                continue
                
        # Sort candidates by absolute price change descending
        anomalies_candidates.sort(key=lambda x: abs(x["price_change"]), reverse=True)
        top_anomalies = anomalies_candidates[:limit_count]
        
        anomalies = []
        
        # Step 4: Iterate through found anomalies and fetch insights sequentially (or concurrently)
        for anomaly in top_anomalies:
            ticker = anomaly["ticker"]
            company_name = anomaly["company_name"]
            price_change_rounded = round(anomaly["price_change"], 2)
            
            logger.info(f"Anomaly detected: {ticker} changed by {price_change_rounded}%")
            
            # Fetch News
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
                "date": str(latest_date), # Approximate local date
                "price_change": price_change_rounded,
                "ai_analysis": ai_analysis,
                "top_news_links": top_news_links
            })
            
        return anomalies
        
    except Exception as e:
        logger.error(f"Error during anomaly detection scan: {e}")
        import traceback
        traceback.print_exc()
        return []
