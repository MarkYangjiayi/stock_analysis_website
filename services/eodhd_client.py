import asyncio
import logging
import random
from typing import Optional, Dict, Any

import httpx

from core.config import settings

# ------------------------------------------------------------------------
# EODHD API Client 配置与基础封装
# ------------------------------------------------------------------------

logger = logging.getLogger(__name__)
# httpx's INFO access log includes the full query string, including EODHD's
# api_token. Keep transport diagnostics at WARNING so credentials never enter
# application logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# 从 Settings 统一加载配置
EODHD_API_KEY = settings.EODHD_API_KEY
EODHD_BASE_URL = settings.EODHD_BASE_URL

# 配置常量
MAX_RETRIES = 5
TIMEOUT_SECONDS = 15.0
INITIAL_BACKOFF = 2.0  # 初始重试延迟（秒）


def create_http_client() -> httpx.AsyncClient:
    """Create one connection pool for a complete sync/backfill operation."""
    return httpx.AsyncClient(timeout=TIMEOUT_SECONDS)


async def _fetch_from_eodhd(
    endpoint: str,
    ticker: str,
    params: Optional[Dict[str, Any]] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[Any]:
    """
    内部通用封装方法：发送异步请求至 EODHD API 并处理重试与限流。
    
    A caller may supply one shared connection pool for a complete pipeline;
    standalone calls own and close their client. Transient failures use
    bounded exponential retries.
    """
    if params is None:
        params = {}
    
    # 强制加上 API_TOKEN
    params["api_token"] = EODHD_API_KEY
    params["fmt"] = "json"  # 强制要求 JSON 格式返回

    url = f"{EODHD_BASE_URL}/{endpoint}/{ticker}"
    
    async def execute(http_client: httpx.AsyncClient) -> Optional[Any]:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = await http_client.get(url, params=params)
                
                # 处理 HTTP 429 Too Many Requests (限流) 以及 5xx 服务器错误等
                if response.status_code in (429, 500, 502, 503, 504):
                    logger.warning(
                        f"Attempt {attempt}/{MAX_RETRIES}: Received status {response.status_code} "
                        f"for {url}. Retrying..."
                    )
                    if attempt < MAX_RETRIES:
                        retry_after = response.headers.get("Retry-After")
                        try:
                            server_delay = float(retry_after) if retry_after else 0.0
                        except ValueError:
                            server_delay = 0.0
                        delay = max(server_delay, INITIAL_BACKOFF * (2 ** (attempt - 1)))
                        await asyncio.sleep(delay + random.uniform(0.0, 0.5))
                        continue
                    else:
                        response.raise_for_status()

                # 处理其它 HTTP 错误
                response.raise_for_status()
                
                # 请求成功
                return response.json()
                
            except httpx.RequestError as e:
                logger.error(f"Attempt {attempt}/{MAX_RETRIES}: RequestError fetching {url}: {e}")
                if attempt < MAX_RETRIES:
                    delay = INITIAL_BACKOFF * (2 ** (attempt - 1))
                    await asyncio.sleep(delay + random.uniform(0.0, 0.5))
                else:
                    logger.error(f"Failed to fetch data from {url} after {MAX_RETRIES} attempts.")
                    return None
            except httpx.HTTPStatusError as e:
                logger.error(f"HTTPStatusError fetching {url}: {e.response.status_code} - {e.response.text}")
                return None
            except ValueError as e:
                logger.error(f"Failed to parse JSON response for {url}: {e}")
                return None

        return None

    if client is not None:
        return await execute(client)
    async with create_http_client() as owned_client:
        return await execute(owned_client)


# ------------------------------------------------------------------------
# 暴露给业务层调用的具体方法
# ------------------------------------------------------------------------

async def get_fundamental_data(
    ticker: str,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[Dict[str, Any]]:
    """
    获取指定股票代码的基本面数据 (Fundamentals)
    
    参数:
    - ticker: 股票代码 (例如: AAPL.US)
    
    返回:
    - dict: 包含完整基本面信息的 JSON 对象，如果失败返回 None。
    """
    logger.debug("Fetching fundamentals for %s", ticker)
    return await _fetch_from_eodhd(endpoint="fundamentals", ticker=ticker, client=client)


async def get_eod_historical_data(
    ticker: str,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[list]:
    """
    获取指定股票的日线历史行情 (EOD/Historical Prices)
    
    参数:
    - ticker: 股票代码 (例如: AAPL.US)
    - from_date: 起始日期，格式 'YYYY-MM-DD' (可选)
    - to_date: 结束日期，格式 'YYYY-MM-DD' (可选)
    
    返回:
    - list: 包含历史行情数据的列表，如果失败返回 None。
    """
    logger.debug("Fetching EOD historical data for %s", ticker)
    params = {}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
        
    return await _fetch_from_eodhd(endpoint="eod", ticker=ticker, params=params, client=client)


async def get_bulk_eod_prices(
    exchange: str = "US",
    date_str: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[list]:
    """
    获取指定交易所全部股票的某一天的批量日终行情
    """
    logger.info(f"Fetching bulk EOD prices for exchange {exchange} on {date_str or 'latest'}...")
    params = {}
    if date_str:
        params["date"] = date_str
    return await _fetch_from_eodhd(endpoint="eod-bulk-last-day", ticker=exchange, params=params, client=client)


async def get_bulk_corporate_actions(
    action_type: str,
    exchange: str = "US",
    date_str: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[list]:
    """Fetch one exchange-wide split or dividend batch for a session."""
    if action_type not in {"splits", "dividends"}:
        raise ValueError("action_type must be splits or dividends")
    params: Dict[str, Any] = {"type": action_type}
    if date_str:
        params["date"] = date_str
    return await _fetch_from_eodhd(
        endpoint="eod-bulk-last-day",
        ticker=exchange,
        params=params,
        client=client,
    )


async def get_bulk_fundamentals(
    exchange: str = "US",
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[list]:
    """
    获取指定交易所的批量基本面数据信息 (Sector, Industry, PE, PB, Market Cap)
    """
    logger.info(f"Fetching bulk fundamentals for exchange {exchange}...")
    return await _fetch_from_eodhd(endpoint="bulk-fundamentals", ticker=exchange, client=client)


async def get_index_components(
    index_ticker: str,
    client: Optional[httpx.AsyncClient] = None,
) -> list[str]:
    """
    Fetches the constituent tickers of a given index.
    
    Args:
        index_ticker: The index symbol (e.g., 'GSPC.INDX' for S&P 500)
    Returns:
        List of ticker strings (e.g., ['AAPL.US', 'MSFT.US'])
    """
    logger.info(f"Fetching components for index {index_ticker}...")
    data = await get_fundamental_data(index_ticker, client=client)
    
    components = []
    if data and "Components" in data:
        for symbol_index, component_data in data["Components"].items():
            code = component_data.get("Code")
            exchange = component_data.get("Exchange")
            if code and exchange:
                components.append(f"{code}.{exchange}")
    
    logger.info(f"Found {len(components)} components for {index_ticker}")
    return components

async def get_bulk_realtime_prices(
    exchange: str = "US",
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[list[Dict[str, Any]]]:
    """
    Fetches real-time prices for an entire exchange (e.g., US) in one API call.
    The EODHD API allows fetching all live data by passing `ex=US` alongside a dummy ticker.
    """
    logger.info(f"Fetching bulk real-time prices for exchange {exchange}...")
    
    dummy_ticker = "AAPL.US"
    params = {"ex": exchange}
        
    response_data = await _fetch_from_eodhd(endpoint="real-time", ticker=dummy_ticker, params=params, client=client)
    
    if response_data is None:
        return None
        
    if isinstance(response_data, list):
        return response_data
    return []


async def get_splits(
    ticker: str,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[list]:
    params: Dict[str, Any] = {}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    return await _fetch_from_eodhd("splits", ticker, params=params, client=client)


async def get_dividends(
    ticker: str,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[list]:
    params: Dict[str, Any] = {}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    return await _fetch_from_eodhd("div", ticker, params=params, client=client)
