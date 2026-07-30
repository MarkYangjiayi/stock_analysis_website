import feedparser
import html
import httpx
import re
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class NewsFetchError(RuntimeError):
    """Raised when the news provider cannot return a trustworthy response."""


def _yahoo_symbol(ticker: str) -> str:
    normalized = ticker.strip()
    return normalized[:-3] if normalized.upper().endswith(".US") else normalized


def _safe_article_link(value: str) -> str:
    normalized = str(value or "").strip()
    parsed = urlparse(normalized)
    return normalized if parsed.scheme in {"http", "https"} and parsed.netloc else ""


async def fetch_yahoo_news(
    ticker: str,
    *,
    lookback_hours: int = 72,
    client: Optional[httpx.AsyncClient] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch the latest Yahoo Finance RSS items inside the requested lookback.
    Entries without trustworthy timestamps or links are discarded.
    Supports a maximum of 10 items.
    """
    clean_ticker = _yahoo_symbol(ticker)
    if not clean_ticker:
        raise NewsFetchError("Ticker is required for news lookup")

    url = "https://feeds.finance.yahoo.com/rss/2.0/headline"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    async def execute(http_client: httpx.AsyncClient) -> List[Dict[str, Any]]:
        try:
            response = await http_client.get(
                url,
                params={"s": clean_ticker},
                headers=headers,
            )
        except httpx.HTTPError as exc:
            logger.warning("Yahoo RSS request failed for %s: %s", ticker, type(exc).__name__)
            raise NewsFetchError("News provider is unavailable") from exc

        response.raise_for_status()
        feed = feedparser.parse(response.content)
        news_items = []

        if getattr(feed, "bozo", False) and not feed.entries:
            raise NewsFetchError("News provider returned an invalid feed")
        if not feed.entries:
            logger.info(f"No news found for {clean_ticker} via Yahoo RSS.")
            return []

        now = datetime.now(timezone.utc)
        cutoff_time = now - timedelta(hours=max(1, lookback_hours))

        for entry in feed.entries:
            if len(news_items) >= 10:
                break

            pub_date = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            if pub_date is None or pub_date < cutoff_time or pub_date > now + timedelta(minutes=5):
                continue

            summary = str(getattr(entry, "summary", "") or "")
            clean_summary = html.unescape(re.sub(r"<[^>]+>", "", summary)).strip()
            link = _safe_article_link(getattr(entry, "link", ""))
            if not link:
                continue

            news_items.append({
                "title": html.unescape(
                    str(getattr(entry, "title", "") or "")
                ).strip(),
                "link": link,
                "pub_date": pub_date.isoformat(),
                "summary": clean_summary,
                "publisher": html.unescape(
                    str(getattr(entry, "publisher", "Yahoo Finance") or "")
                ).strip() or "Yahoo Finance",
            })

        return news_items

    try:
        if client is not None:
            return await execute(client)
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as owned_client:
            return await execute(owned_client)
    except NewsFetchError:
        raise
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Yahoo RSS returned HTTP %s for %s",
            exc.response.status_code,
            ticker,
        )
        raise NewsFetchError("News provider is unavailable") from exc
    except Exception as exc:
        logger.exception("Unexpected news parsing failure for %s", ticker)
        raise NewsFetchError("News provider returned an invalid response") from exc
