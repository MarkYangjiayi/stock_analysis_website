import feedparser
import requests
import re
from datetime import datetime, timedelta, timezone
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

def fetch_yahoo_news(ticker: str) -> List[Dict[str, Any]]:
    """
    Fetches the latest news for a given ticker from Yahoo Finance RSS.
    Returns only news from the past 72 hours, with stripped HTML.
    Supports a maximum of 10 items.
    """
    try:
        # Clean ticker: e.g. AAPL.US -> AAPL
        clean_ticker = ticker.split('.')[0]
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={clean_ticker}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        feed = feedparser.parse(response.content)
        news_items = []
        
        if not feed.entries:
            logger.info(f"No news found for {clean_ticker} via Yahoo RSS.")
            return []
            
        now = datetime.now(timezone.utc)
        cutoff_time = now - timedelta(hours=72)
        
        for entry in feed.entries:
            if len(news_items) >= 10:
                break
                
            # Parse publication date
            # Yahoo RSS usually provides 'published_parsed'
            pub_date = None
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            
            # Skip if older than 72 hours or date couldn't be parsed
            if pub_date and pub_date < cutoff_time:
                continue
                
            # Clean HTML from summary
            summary = getattr(entry, 'summary', '')
            clean_summary = re.sub(r'<[^>]+>', '', summary).strip()
            
            news_items.append({
                "title": getattr(entry, 'title', ''),
                "link": getattr(entry, 'link', ''),
                "pub_date": pub_date.isoformat() if pub_date else None,
                "summary": clean_summary,
                "publisher": getattr(entry, 'publisher', 'Yahoo Finance')
            })
            
        return news_items
        
    except Exception as e:
        logger.error(f"Error fetching news for {ticker}: {e}")
        return []

if __name__ == '__main__':
    # Test script
    import json
    news = fetch_yahoo_news('AAPL.US')
    print(json.dumps(news, indent=2))
