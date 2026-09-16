"""Bounded cross-asset evidence collection for the two scheduled reports.

Snapshots are indicative/delayed, never labelled real-time or official closes.
Calendars determine freshness in each listing's local session. Daily fallback
data retains its own date and is excluded from current-session conclusions.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import logging
import math
import re
from typing import Any
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd
import pandas_market_calendars as market_calendars
from sqlalchemy import select

from core.config import settings
from database import async_session_maker
from models import DataPublication, MarketBreadthSnapshot, PipelineRun
from services import eodhd_client
from services.personal_workspace import get_watchlist
from services.rrg_prices import RRG_SECTOR_NAMES
from services.yield_curve import get_yield_curve

logger = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
QUOTE_SOURCE = "https://eodhd.com/financial-apis/live-ohlcv-stocks-api"
EOD_SOURCE = "https://eodhd.com/financial-apis/historical-eod-data-api"
ECONOMIC_SOURCE = "https://eodhd.com/financial-apis/economic-events-data-api"
EARNINGS_SOURCE = "https://eodhd.com/financial-apis/calendar-upcoming-earnings-ipos-and-splits"


@dataclass(frozen=True)
class Instrument:
    ticker: str
    name: str
    group: str
    currency: str = "USD"
    calendar: str | None = "XNYS"
    proxy: str | None = None


PRECIOUS_METALS = (
    Instrument("GLD.US", "黄金", "commodity", proxy="GLD ETF"),
    Instrument("SLV.US", "白银", "commodity", proxy="SLV ETF"),
    Instrument("PPLT.US", "铂金", "commodity", proxy="PPLT ETF"),
    Instrument("PALL.US", "钯金", "commodity", proxy="PALL ETF"),
)
PRECIOUS_METAL_TICKERS = frozenset(asset.ticker for asset in PRECIOUS_METALS)


MARKET_INSTRUMENTS = (
    Instrument("SPY.US", "标普500", "equity", proxy="SPY ETF"),
    Instrument("QQQ.US", "纳斯达克100", "equity", proxy="QQQ ETF"),
    Instrument("IWM.US", "罗素2000", "equity", proxy="IWM ETF"),
    Instrument("CSI300.INDX", "沪深300", "equity", "CNY", "XSHG"),
    Instrument("HSI.INDX", "恒生指数", "equity", "HKD", "XHKG"),
    Instrument("HSTECH.INDX", "恒生科技", "equity", "HKD", "XHKG"),
    Instrument("N225.INDX", "日经225", "equity", "JPY", "XTKS"),
    Instrument("TWII.INDX", "台湾加权", "equity", "TWD", "XTAI"),
    Instrument("STOXX50E.INDX", "欧洲斯托克50", "equity", "EUR", "XETR"),
    Instrument("NYICDX.INDX", "美元指数", "fx", "点", None),
    Instrument("USDCNH.FOREX", "USD/CNH", "fx", "CNH/USD", None),
    Instrument("USDJPY.FOREX", "USD/JPY", "fx", "JPY/USD", None),
    Instrument("EURUSD.FOREX", "EUR/USD", "fx", "USD/EUR", None),
    *PRECIOUS_METALS,
    Instrument("USO.US", "WTI原油", "commodity", proxy="USO期货ETF"),
    Instrument("CPER.US", "铜", "commodity", proxy="CPER期货ETF"),
    Instrument("TLT.US", "长期美债价格", "credit", proxy="TLT ETF"),
    Instrument("HYG.US", "高收益公司债价格", "credit", proxy="HYG ETF，非信用利差"),
    Instrument("LQD.US", "投资级公司债价格", "credit", proxy="LQD ETF，非信用利差"),
    Instrument("VIX.INDX", "VIX", "risk", "点"),
    Instrument("BTC-USD.CC", "BTC 比特币", "risk", calendar=None),
    Instrument("ETH-USD.CC", "ETH 以太坊", "risk", calendar=None),
    Instrument("RSP.US", "标普等权", "structure", proxy="RSP ETF"),
)
CORE_NAMES = {
    "AAPL.US": "苹果", "MSFT.US": "微软", "NVDA.US": "英伟达",
    "AMZN.US": "亚马逊", "0700.HK": "腾讯", "9988.HK": "阿里巴巴",
    "2330.TW": "台积电", "ASML.AS": "ASML",
}
SECTOR_NAMES = {
    "XLK.US": "科技", "XLF.US": "金融", "XLV.US": "医疗保健", "XLY.US": "可选消费",
    "XLP.US": "必需消费", "XLE.US": "能源", "XLI.US": "工业", "XLB.US": "原材料",
    "XLU.US": "公用事业", "XLRE.US": "房地产", "XLC.US": "通信服务",
}
EXCHANGES = {
    "US": ("XNYS", "USD"), "HK": ("XHKG", "HKD"),
    "SHG": ("XSHG", "CNY"), "SHE": ("XSHG", "CNY"),
    "TW": ("XTAI", "TWD"), "TSE": ("XTKS", "JPY"),
    "AS": ("XAMS", "EUR"), "XETRA": ("XETR", "EUR"),
}


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _positive(value: Any) -> float | None:
    parsed = number(value)
    return parsed if parsed is not None and parsed > 0 else None


def _return(current: Any, previous: Any) -> float | None:
    a, b = _positive(current), _positive(previous)
    return round((a / b - 1) * 100, 4) if a is not None and b is not None else None


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@lru_cache(maxsize=16)
def _calendar(name: str):
    return xcals.get_calendar(name)


@lru_cache(maxsize=64)
def _schedule(name: str, local_date: date):
    # The separately pinned pandas_market_calendars contains the 2026 China
    # holiday list and the Tokyo 15:30 close introduced in November 2024.
    # exchange_calendars 4.5.6 stops XSHG at 2025 and still closes Tokyo at 15:00.
    mapping = {"XNYS": "NYSE", "XHKG": "HKEX", "XSHG": "SSE", "XTKS": "JPX"}
    calendar = market_calendars.get_calendar(mapping.get(name, name))
    return calendar, calendar.schedule(start_date=local_date - timedelta(days=40), end_date=local_date)


def session_context(asset: Instrument, now: datetime) -> dict:
    """Use the actual exchange calendar, including holidays and shortened days."""
    now = aware(now)
    if asset.calendar is None:
        return {"state": "连续报价", "expected_date": now.date().isoformat(), "timezone": "UTC"}
    zones = {"XNYS": "America/New_York", "XHKG": "Asia/Hong_Kong", "XSHG": "Asia/Shanghai",
             "XTKS": "Asia/Tokyo", "XTAI": "Asia/Taipei", "XAMS": "Europe/Amsterdam", "XETR": "Europe/Berlin"}
    zone = zones[asset.calendar]
    local_date = now.astimezone(ZoneInfo(zone)).date()
    try:
        _, schedule = _schedule(asset.calendar, local_date)
        position = len(schedule) - 1
        session, row = schedule.index[position], schedule.iloc[position]
        opens, closes = row["market_open"], row["market_close"]
        if session.date() == local_date and now < opens:
            state = "未开盘"
            position -= 1
            session, row = schedule.index[position], schedule.iloc[position]
        elif now >= closes:
            state = "已收盘" if session.date() == local_date else "休市"
        else:
            start, end = row.get("break_start"), row.get("break_end")
            state = "午间休市" if pd.notna(start) and start <= now < end else "盘中"
        return {
            "state": state, "expected_date": session.date().isoformat(), "timezone": zone,
            "previous_date": schedule.index[position - 1].date().isoformat(),
            "open": row["market_open"].isoformat(), "close": row["market_close"].isoformat(),
        }
    except Exception as exc:
        logger.warning("Report calendar %s unavailable (%s)", asset.calendar, type(exc).__name__)
        return {"state": "交易日历不可用", "expected_date": None, "timezone": zone}


def _empty(asset: Instrument, now: datetime) -> dict:
    session = session_context(asset, now)
    return {
        **asdict(asset), "session": session, "price": None,
        "previous_close": None, "change_pct": None, "from_open_pct": None,
        "as_of": None, "session_date": None, "source": "EODHD",
        "source_url": QUOTE_SOURCE, "basis": "delayed_snapshot",
        "fresh": False, "status": "unavailable", "status_label": "数据不可用",
    }


def normalize_quote(asset: Instrument, raw: dict, now: datetime) -> dict:
    result = _empty(asset, now)
    price, timestamp = _positive(raw.get("close")), _positive(raw.get("timestamp"))
    if str(raw.get("code", "")).upper() != asset.ticker or price is None or timestamp is None:
        return result
    try:
        quoted_at = datetime.fromtimestamp(timestamp, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return result
    if quoted_at > aware(now) + timedelta(minutes=2):
        result["status_label"] = "报价时间异常"
        return result
    session = result["session"]
    quoted_date = quoted_at.astimezone(ZoneInfo(session["timezone"])).date().isoformat()
    result.update({
        "price": price, "previous_close": _positive(raw.get("previousClose")),
        "change_pct": _return(price, raw.get("previousClose")),
        "open": _positive(raw.get("open")), "as_of": quoted_at.isoformat(),
        "session_date": quoted_date,
        "status": "available", "fresh": True, "status_label": "延迟报价",
    })
    age = aware(now) - quoted_at
    if asset.calendar is None:
        # FX/crypto's provider previousClose is not asserted to be a 24h return.
        max_age_minutes = 5 if asset.ticker.endswith(".FOREX") else 10 if asset.ticker.endswith(".CC") else 45
        if age > timedelta(minutes=max_age_minutes):
            result.update(fresh=False, status="stale", status_label="旧报价")
    elif session["expected_date"] is None:
        result.update(fresh=False, status="stale", status_label="交易日无法核验")
    elif quoted_date != session["expected_date"]:
        result.update(fresh=False, status="stale", status_label="非当前交易日报价")
    else:
        opens, closes = datetime.fromisoformat(session["open"]), datetime.fromisoformat(session["close"])
        if quoted_at < opens:
            result.update(fresh=False, status="stale", status_label="开盘行情待更新")
        elif session["state"] == "盘中" and age > timedelta(minutes=45):
            result.update(fresh=False, status="stale", status_label="盘中报价过期")
        elif aware(now) >= closes and quoted_at < closes - timedelta(minutes=5):
            result.update(fresh=False, status="partial", status_label="收盘行情待更新")
        elif quoted_at > closes + timedelta(minutes=5):
            result["status_label"] = "收盘后快照，非正式收盘价"
        elif aware(now) >= closes:
            result["status_label"] = "收盘附近延迟报价"
        if opens <= quoted_at:
            result["from_open_pct"] = _return(price, raw.get("open"))
    return result


def normalize_eod(asset: Instrument, rows: list[dict], now: datetime) -> dict:
    result = _empty(asset, now)
    session = result["session"]
    through = session["expected_date"] or (aware(now).date() - timedelta(days=1)).isoformat()
    if asset.calendar is None:
        # A continuous-market daily bar dated today may still be forming.
        through = (aware(now).date() - timedelta(days=1)).isoformat()
    if asset.calendar and session.get("close") and aware(now) < datetime.fromisoformat(session["close"]):
        through = session["previous_date"]
    by_date = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        try:
            key = date.fromisoformat(str(raw.get("date"))).isoformat()
        except ValueError:
            continue
        if key <= through and _positive(raw.get("close")) is not None:
            by_date[key] = raw
    if not by_date:
        return result
    keys = sorted(by_date)
    last = by_date[keys[-1]]
    previous = by_date[keys[-2]] if len(keys) > 1 else {}
    # Adjusted closes prevent splits/distributions becoming false anomalies.
    adjusted = all(_positive(row.get("adjusted_close")) is not None for row in (last, previous))
    basis = "adjusted_close" if adjusted else "close"
    fresh = keys[-1] == session["expected_date"]
    result.update({
        "price": float(last["close"]), "as_of": keys[-1], "session_date": keys[-1],
        "previous_date": keys[-2] if len(keys) > 1 else None,
        "previous_close": _positive(previous.get("close")),
        "change_pct": _return(last.get(basis), previous.get(basis)),
        "from_open_pct": _return(last["close"], last.get("open")),
        "open": _positive(last.get("open")), "source_url": EOD_SOURCE,
        "basis": "eod_adjusted" if adjusted else "eod_close", "fresh": fresh,
        "status": "available" if fresh else "stale",
        "status_label": "日线收盘" if fresh else "历史日线（当期行情缺失）",
    })
    return result


async def report_instruments() -> tuple[list[Instrument], list[str]]:
    assets = [*MARKET_INSTRUMENTS, *(
        Instrument(ticker, SECTOR_NAMES.get(ticker, label), "sector", proxy="板块ETF")
        for ticker, label in RRG_SECTOR_NAMES.items()
    )]
    warnings: list[str] = []
    core = [symbol.strip().upper() for symbol in settings.DAILY_REPORT_CORE_SYMBOLS.split(",") if symbol.strip()]
    if len(core) > 30:
        warnings.append("核心资产配置超过30只，已仅采用前30只。")
    symbols = core[:30]
    if settings.DAILY_REPORT_INCLUDE_WATCHLIST:
        try:
            async with async_session_maker() as db:
                watchlist = await get_watchlist(db)
            remaining = [ticker for ticker in watchlist if ticker not in symbols]
            symbols += remaining[:settings.DAILY_REPORT_WATCHLIST_LIMIT]
            if len(remaining) > settings.DAILY_REPORT_WATCHLIST_LIMIT:
                warnings.append(f"自选股仅展示前{settings.DAILY_REPORT_WATCHLIST_LIMIT}只额外标的。")
        except Exception:
            warnings.append("自选股读取失败，使用固定核心资产池。")
    existing = {asset.ticker for asset in assets}
    for ticker in dict.fromkeys(symbols):
        if not re.fullmatch(r"[A-Z0-9^_-]+(?:\.[A-Z0-9_-]+)+", ticker):
            warnings.append("已跳过格式无效的核心资产代码（需包含交易所后缀）。")
            continue
        if ticker in existing:
            continue
        exchange = ticker.rsplit(".", 1)[-1]
        if exchange not in EXCHANGES:
            warnings.append(f"{ticker} 尚无交易日历映射，未加入核心资产池。")
            continue
        calendar, currency = EXCHANGES[exchange]
        assets.append(Instrument(ticker, CORE_NAMES.get(ticker, ticker), "core", currency, calendar))
        existing.add(ticker)
    return assets, warnings


async def collect_quotes(assets: list[Instrument], now: datetime) -> list[dict]:
    results = {asset.ticker: _empty(asset, now) for asset in assets}
    by_ticker = {asset.ticker: asset for asset in assets}
    semaphore = asyncio.Semaphore(4)
    async with eodhd_client.create_http_client() as client:
        async def batch(group):
            async with semaphore:
                try:
                    rows = await asyncio.wait_for(eodhd_client.get_live_quotes(group, client=client), 15)
                    for raw in rows:
                        ticker = str(raw.get("code", "")).upper()
                        if ticker in group:
                            results[ticker] = normalize_quote(by_ticker[ticker], raw, now)
                except Exception as exc:
                    logger.warning("Report quote batch unavailable (%s)", type(exc).__name__)
        symbols = list(by_ticker)
        await asyncio.gather(*(batch(symbols[i:i + 20]) for i in range(0, len(symbols), 20)))

        async def fallback(asset):
            async with semaphore:
                try:
                    rows = await asyncio.wait_for(eodhd_client.get_eod_historical_data(
                        asset.ticker, (now.date() - timedelta(days=14)).isoformat(),
                        now.date().isoformat(), client=client,
                    ), 10)
                    daily = normalize_eod(asset, rows or [], now)
                    current = results[asset.ticker]
                    if daily["price"] is not None and (
                        current["price"] is None or daily["fresh"]
                        or (daily["session_date"] > (current["session_date"] or ""))
                    ):
                        results[asset.ticker] = daily
                except Exception as exc:
                    logger.warning("Report daily fallback unavailable for %s (%s)", asset.ticker, type(exc).__name__)
        candidates = [asset for asset in assets if not results[asset.ticker]["fresh"]]
        try:
            await asyncio.wait_for(asyncio.gather(*(fallback(asset) for asset in candidates)), 30)
        except TimeoutError:
            logger.warning("Report daily fallback budget exhausted; retaining completed observations")
    return [results[asset.ticker] for asset in assets]


def normalize_rates(payload: dict, now: datetime) -> dict:
    observations = sorted(
        (row for row in payload.get("observations", []) if row.get("date", "") <= now.astimezone(NY).date().isoformat()),
        key=lambda row: row["date"],
    )
    if not observations:
        raise ValueError("No dated Treasury observations")
    last = observations[-1]
    previous = observations[-2] if len(observations) > 1 else {"yields": {}}
    values = []
    for tenor in ("2y", "10y", "30y"):
        current, before = number(last["yields"].get(tenor)), number(previous["yields"].get(tenor))
        values.append({"tenor": tenor, "yield_pct": current,
                       "change_bp": round((current - before) * 100, 2) if current is not None and before is not None else None})
    two, ten = number(last["yields"].get("2y")), number(last["yields"].get("10y"))
    previous_two, previous_ten = number(previous["yields"].get("2y")), number(previous["yields"].get("10y"))
    spread = round((ten - two) * 100, 2) if two is not None and ten is not None else None
    previous_spread = (previous_ten - previous_two) * 100 if previous_two is not None and previous_ten is not None else None
    return {"as_of": last["date"], "previous_date": previous.get("date"), "values": values,
            "spread_10y_2y_bp": spread,
            "spread_change_bp": round(spread - previous_spread, 2) if spread is not None and previous_spread is not None else None,
            "meta": payload.get("meta", {})}


async def collect_breadth(now: datetime) -> dict:
    async with async_session_maker() as db:
        row = await db.scalar(
            select(MarketBreadthSnapshot)
            .join(DataPublication, DataPublication.pipeline_run_id == MarketBreadthSnapshot.pipeline_run_id)
            .join(PipelineRun, PipelineRun.id == DataPublication.pipeline_run_id)
            .where(DataPublication.dataset == "market_breadth", DataPublication.status == "published",
                   PipelineRun.status == "published", MarketBreadthSnapshot.universe == "SP500",
                   MarketBreadthSnapshot.date <= now.astimezone(NY).date(),
                   MarketBreadthSnapshot.date == DataPublication.as_of_date)
            .order_by(MarketBreadthSnapshot.date.desc()).limit(1)
        )
        if row is None:
            raise ValueError("No published market breadth")
        def percentage(numerator, denominator):
            return round(numerator / denominator * 100, 2) if denominator else None
        return {
            "as_of": row.date.isoformat(), "universe": "S&P 500 历史成分",
            "advances": row.advances, "declines": row.declines, "unchanged": row.unchanged,
            "pct_above_ma20": percentage(row.above_ma20, row.ma20_eligible),
            "pct_above_ma50": percentage(row.above_ma50, row.ma50_eligible),
            "pct_above_ma200": percentage(row.above_ma200, row.ma200_eligible),
            "coverage_pct": percentage(row.price_count, row.member_count),
        }


async def collect_events(assets: list[Instrument], now: datetime) -> dict:
    today = now.astimezone(NY).date()
    calendar = _calendar("XNYS")
    next_day = calendar.date_to_session(pd.Timestamp(today + timedelta(days=1)), direction="next").date()
    async with eodhd_client.create_http_client() as client:
        responses = await asyncio.gather(*(
            eodhd_client.get_report_calendar(kind, today.isoformat(), next_day.isoformat(), client=client)
            for kind in ("economic", "earnings")
        ), return_exceptions=True)
    economic, earnings = responses
    watched = {asset.ticker for asset in assets if asset.group == "core"}
    important = re.compile(r"inflation|consumer price|\bcpi\b|\bpce\b|non.?farm|unemployment|interest rate|fed |fomc|\bgdp\b|retail sales|\bism\b|\bpmi\b|jobless", re.I)
    events = []
    if isinstance(economic, list):
        for row in economic:
            if not isinstance(row, dict) or row.get("country") != "US" or not important.search(str(row.get("type", ""))):
                continue
            event_time = str(row.get("date", ""))
            if not today.isoformat() <= event_time[:10] <= next_day.isoformat():
                continue
            events.append({"kind": "economic", "name": str(row["type"])[:160], "date": event_time,
                           "period": row.get("period"), "comparison": row.get("comparison"),
                           "actual": number(row.get("actual")), "estimate": number(row.get("estimate")),
                           "source_url": ECONOMIC_SOURCE})
    if isinstance(earnings, list):
        for row in earnings:
            if not isinstance(row, dict) or row.get("code") not in watched:
                continue
            report_date = str(row.get("report_date", ""))
            if not today.isoformat() <= report_date <= next_day.isoformat():
                continue
            events.append({"kind": "earnings", "name": row["code"], "date": report_date,
                           "session": row.get("before_after_market"), "source_url": EARNINGS_SOURCE})
    return {"from": today.isoformat(), "to": next_day.isoformat(),
            "economic_available": isinstance(economic, list), "earnings_available": isinstance(earnings, list),
            # Retain bounded evidence before presentation grouping. Truncating
            # at 12 here let repeated projections crowd out the next session.
            "items": sorted(events, key=lambda item: item["date"])[:100], "total": len(events),
            "timezone_note": "经济日历时间按供应商原文展示，接口未提供时区；财报日期按上市市场。"}


async def collect_market_context(*, now: datetime | None = None) -> dict:
    now = aware(now or datetime.now(timezone.utc))
    assets, warnings = await report_instruments()

    async def optional(label, operation, timeout=25):
        try:
            return await asyncio.wait_for(operation, timeout)
        except Exception as exc:
            logger.warning("Report %s unavailable (%s)", label, type(exc).__name__)
            warnings.append(f"{label}暂不可用。")
            return None

    async def rates():
        session = session_context(MARKET_INSTRUMENTS[0], now)
        after_close = session["state"] == "已收盘" and session["expected_date"] == now.astimezone(NY).date().isoformat()
        return normalize_rates(await get_yield_curve("1y", now=now, force_refresh=after_close), now)

    operations = [optional("跨资产行情", collect_quotes(assets, now), 65),
                  optional("美债曲线", rates()), optional("市场宽度", collect_breadth(now))]
    if settings.DAILY_REPORT_EVENTS_ENABLED:
        operations.append(optional("事件日历", collect_events(assets, now)))
    results = await asyncio.gather(*operations)
    quotes, treasury, breadth = results[:3]
    return {"schema_version": 1, "report_date": now.astimezone(NY).date().isoformat(),
            "captured_at": now.isoformat(), "quotes": quotes or [_empty(asset, now) for asset in assets],
            "treasury": treasury, "breadth": breadth,
            "events": results[3] if len(results) > 3 else {"disabled": True}, "warnings": warnings}
