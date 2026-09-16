"""Deterministic market sections using only the saved report snapshot.

Compact lists work in both the full audit text and the foldable Feishu card;
the report does not depend on client-specific table rendering.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import re
from typing import Any

from services.report_market import PRECIOUS_METALS, PRECIOUS_METAL_TICKERS, number


def compact(raw: Any) -> str:
    return " ".join(str(raw or "").split()).replace("<", "＜").replace(">", "＞")


def value(raw: Any, suffix: str = "", signed: bool = False, digits: int = 2) -> str:
    parsed = number(raw)
    if parsed is None:
        return "—"
    if parsed and round(parsed, digits) == 0 and (signed or suffix in {"%", " bp", " 点"}):
        # Preserve the direction of small observations without a misleading
        # signed zero (or inventing a material move by rounding up).
        direction = "微升" if parsed > 0 else "微降"
        return f"{direction}（不足 {10 ** -digits:.{digits}f}{suffix}）"
    if parsed == 0:
        parsed = 0.0
        signed = False
    return (f"{parsed:+,.{digits}f}" if signed else f"{parsed:,.{digits}f}") + suffix


def instant(raw: Any) -> datetime | None:
    """Require an explicit timezone; never use the rendering host's timezone."""
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def breadth_notice(context: dict) -> str | None:
    breadth = context.get("breadth")
    if not breadth:
        return None
    try:
        age = (date.fromisoformat(context["report_date"]) - date.fromisoformat(breadth["as_of"])).days
    except (KeyError, TypeError, ValueError):
        return "市场宽度日期无法核验，不展示指标。"
    if age < 0:
        return "市场宽度日期晚于报告日期，不展示指标。"
    if age > 7:
        return f"市场宽度过旧（截至 {breadth['as_of']}，超过7天），不展示过时指标。"
    return None


def quality_summary(context: dict) -> list[str]:
    quotes = context.get("quotes", [])
    current = sum(row.get("price") is not None and bool(row.get("fresh")) for row in quotes)
    historical = sum(row.get("price") is not None and not row.get("fresh") for row in quotes)
    lines = [f"- 行情当期可用 {current}/{len(quotes)}；历史或待更新 {historical}；缺失 {len(quotes) - current - historical}。"]
    unavailable = []
    if not context.get("treasury"):
        unavailable.append("美债曲线")
    if not context.get("breadth"):
        unavailable.append("市场宽度")
    events = context.get("events")
    if not events:
        unavailable.append("事件日历")
    elif not events.get("disabled"):
        if not events.get("economic_available"):
            unavailable.append("经济日历")
        if not events.get("earnings_available"):
            unavailable.append("财报日历")
    if unavailable:
        lines.append("- 模块缺口：" + "；".join(f"{name}暂不可用" for name in unavailable) + "。")
    if notice := breadth_notice(context):
        lines.append("- " + notice)
    covered = {f"{name}暂不可用。" for name in unavailable}
    lines.extend(f"- {compact(warning)}" for warning in dict.fromkeys(context.get("warnings", [])) if warning not in covered)
    if any(row.get("calendar") == "XNYS" and row.get("session", {}).get("state") == "未开盘" for row in quotes):
        lines.append("- 美股尚未开盘；美股ETF/个股使用前一交易日观察。")
    return lines


def stamp(row: dict) -> str:
    raw = row.get("as_of")
    if not raw:
        return "时间未知"
    if "T" not in raw:
        return raw
    try:
        return datetime.fromisoformat(raw).astimezone(timezone.utc).strftime("%m-%d %H:%M UTC")
    except ValueError:
        return "时间未知"


def quote_line(row: dict, report_type: str, report_date: str | None = None) -> str:
    name, ticker = compact(row.get("name")), compact(row.get("ticker"))
    proxy = f"；代理：{compact(row['proxy'])}" if row.get("proxy") else ""
    label = f"{name}（{ticker}{proxy}）" if name and name != ticker else ticker + (f"（{proxy[1:]}）" if proxy else "")
    if row.get("price") is None:
        return f"- {label}：数据不可用"
    basis = "；复权变动" if row.get("basis") == "eod_adjusted" else ""
    if row.get("basis", "").startswith("eod_"):
        basis += f"；对比日 {row.get('previous_date') or '未知'}"
    state = row.get("session", {}).get("state", "状态未知")
    status = compact(row.get("status_label")) or state
    # Keep all exceptional status text. Only shorten known, normal labels.
    if row.get("fresh"):
        status = {"收盘后快照，非正式收盘价": "盘后快照", "收盘附近延迟报价": "收盘附近报价"}.get(status, status)
        if state == "未开盘":
            status = "前一交易日 · " + status
    details = ""
    if (report_type == "morning_briefing" and row.get("calendar") == "XNYS"
            and row.get("group") in {"equity", "core"} and row.get("fresh")
            and report_date is not None and row.get("session_date") == report_date
            and state != "未开盘" and row.get("from_open_pct") is not None):
        details = f"；开盘以来 {value(row['from_open_pct'], '%', True)}"
    price = value(row.get("price"), digits=4 if row.get("group") == "fx" else 2)
    unit = "点" if ticker.endswith(".INDX") else compact(row.get("currency"))
    if ticker == "VIX.INDX" and number(row.get("previous_close")) is not None:
        details += f"；变动 {value(row['price'] - row['previous_close'], ' 点', True)}"
    return (f"- {label}：{price} {unit} / {value(row.get('change_pct'), '%', True)}"
            f"{details}；{stamp(row)} · {status}{basis}")


def _compatible(a: dict | None, b: dict | None) -> bool:
    if not a or not b or not a.get("fresh") or not b.get("fresh"):
        return False
    if a.get("session_date") != b.get("session_date") or a.get("basis") != b.get("basis"):
        return False
    try:
        return abs((datetime.fromisoformat(a["as_of"]) - datetime.fromisoformat(b["as_of"])).total_seconds()) <= 300
    except (KeyError, ValueError, TypeError):
        return False


def morning_comparison(current: dict, morning: dict | None) -> list[str]:
    if not morning or morning.get("report_date") != current.get("report_date"):
        return ["- 当天无已送达的开盘报告快照，暂无法比较。"]
    by_symbol = {row["ticker"]: row for row in morning.get("quotes", [])}
    lines = []
    for row in current.get("quotes", []):
        if row.get("ticker") not in {"SPY.US", "QQQ.US", "IWM.US", "VIX.INDX", "BTC-USD.CC", "ETH-USD.CC", *PRECIOUS_METAL_TICKERS}:
            continue
        previous = by_symbol.get(row["ticker"])
        if (not previous or not previous.get("fresh") or not row.get("fresh")
                or previous.get("session_date") != row.get("session_date")
                or not previous.get("as_of") or not row.get("as_of")):
            continue
        # Daily closing records follow the morning snapshot of that session.
        if "T" in row["as_of"] and datetime.fromisoformat(row["as_of"]) <= datetime.fromisoformat(previous["as_of"]):
            continue
        before, after = number(previous.get("price")), number(row.get("price"))
        if before is None or after is None or before <= 0:
            continue
        lines.append(f"- {row['name']}：自早报报价以来 {value((after / before - 1) * 100, '%', True)}"
                     f"（{stamp(previous)} → {stamp(row)}）")
    return lines or ["- 暂无同一交易日、时间有效的前后报价可供比较。"]


def _focus_quote(ticker: str, name: str, by_symbol: dict, *, show_price: bool = False) -> str:
    row = by_symbol.get(ticker)
    if not row:
        return f"{name} 未纳入快照"
    if row.get("price") is None:
        return f"{name} 数据不可用"
    if not row.get("fresh"):
        return f"{name} 历史／待更新"
    move = value(row.get("change_pct"), "%", True) if number(row.get("change_pct")) is not None else "涨跌未知"
    if show_price:
        return f"{name} {value(row['price'])} USD / {move}（{stamp(row)}）"
    return f"{name} {move}"


def _display_group(row: dict) -> str:
    # Route old saved snapshots by ticker too; no evidence migration is needed.
    if row.get("ticker") in PRECIOUS_METAL_TICKERS:
        return "precious"
    if str(row.get("ticker", "")).endswith(".CC"):
        return "crypto"
    return row.get("group", "")


def render_market_sections(context: dict, report_type: str) -> list[str]:
    quotes = context.get("quotes", [])
    by_symbol = {row["ticker"]: row for row in quotes}
    fresh = [row for row in quotes if row.get("fresh") and row.get("change_pct") is not None]
    lines = ["**核心变化**", ""]
    us = [by_symbol[ticker] for ticker in ("SPY.US", "QQQ.US", "IWM.US")
          if ticker in by_symbol and by_symbol[ticker] in fresh]
    lines.append("- 美股基准ETF：" + ("；".join(f"{row['ticker'].split('.')[0]} {value(row['change_pct'], '%', True)}" for row in us)
                                      if us else "当前交易日有效报价不足。"))
    overseas = [row for row in fresh if row.get("group") == "equity" and row.get("calendar") != "XNYS"]
    if len(overseas) == 1:
        row = overseas[0]
        lines.append(f"- 海外仅1个市场可比：{row['name']} {value(row['change_pct'], '%', True)}。")
    elif overseas:
        leader, laggard = max(overseas, key=lambda row: row["change_pct"]), min(overseas, key=lambda row: row["change_pct"])
        lines.append(f"- 海外可用市场中最强：{leader['name']} {value(leader['change_pct'], '%', True)}；"
                     f"最弱：{laggard['name']} {value(laggard['change_pct'], '%', True)}。")
    else:
        lines.append("- 海外市场当前交易日有效报价不足，不能判断相对强弱。")
    risk = [by_symbol[ticker] for ticker in ("VIX.INDX", "NYICDX.INDX")
            if ticker in by_symbol and by_symbol[ticker] in fresh]
    lines.append("- 跨资产观察：" + ("；".join(f"{row['name']}{'（ETF代理）' if row.get('proxy') else ''} {value(row['change_pct'], '%', True)}" for row in risk)
                                        if risk else "有效报价不足。"))
    lines.append("- 贵金属ETF：" + "；".join(_focus_quote(asset.ticker, asset.name, by_symbol) for asset in PRECIOUS_METALS) + "。")
    lines.append("- " + _focus_quote("BTC-USD.CC", "BTC 比特币", by_symbol, show_price=True) + "；较前收盘，非24小时涨跌。")
    for group, label in (("equity", "全球股票市场"), ("fx", "外汇"), ("precious", "贵金属（ETF代理）"),
                         ("commodity", "能源与工业金属（ETF代理）"), ("credit", "债券价格代理"),
                         ("risk", "波动率"), ("crypto", "加密资产（BTC / ETH）")):
        rows = [row for row in quotes if _display_group(row) == group]
        if rows:
            lines.extend(["", f"**{label}**", ""])
            lines.extend(quote_line(row, report_type, context.get("report_date")) for row in rows)
    treasury = context.get("treasury")
    if treasury:
        lines.extend(["", "**美债收益率（日频）**", ""])
        lines.append(f"观察日 {treasury['as_of']}；变化相对 {treasury.get('previous_date') or '未知日期'}，并非盘中利率。")
        for row in treasury.get("values", []):
            lines.append(f"- {row['tenor'].upper()}：{value(row.get('yield_pct'), '%')} / {value(row.get('change_bp'), ' bp', True)}")
        lines.append(f"- 10Y−2Y：{value(treasury.get('spread_10y_2y_bp'), ' bp', True)}；"
                     f"变化 {value(treasury.get('spread_change_bp'), ' bp', True)}")
        if treasury.get("meta", {}).get("stale"):
            lines.append("- 数据提示：使用较早观察或回退缓存。")
    lines.extend(["", "**板块与市场宽度**", ""])
    sectors = sorted((row for row in fresh if row.get("group") == "sector"), key=lambda row: row["change_pct"], reverse=True)
    if sectors:
        lines.append(f"板块ETF有效覆盖 {len(sectors)}/11；相对各自前收盘：")
        lines.extend(f"- {row['name']}（{row['ticker'].split('.')[0]}）{value(row['change_pct'], '%', True)}（{stamp(row)}）" for row in sectors)
    else:
        lines.append("- 当期板块行情不足，暂不进行强弱排序。")
    rsp, spy = by_symbol.get("RSP.US"), by_symbol.get("SPY.US")
    if _compatible(rsp, spy) and rsp.get("change_pct") is not None and spy.get("change_pct") is not None:
        relative = ((1 + rsp["change_pct"] / 100) / (1 + spy["change_pct"] / 100) - 1) * 100
        lines.append(f"- RSP/SPY 相对前收盘变化 {value(relative, '%', True)}（同日、相近时点报价）。")
    else:
        lines.append("- RSP/SPY：缺乏同日、相近时点且口径一致的报价。")
    breadth = context.get("breadth")
    if breadth and breadth_notice(context) is None:
        lines.append(f"- 市场宽度：截至 {breadth['as_of']} 的已发布日线，{breadth['universe']}；"
                     f"涨 {breadth['advances']} / 跌 {breadth['declines']} / 平 {breadth['unchanged']}；"
                     f"价格覆盖 {value(breadth.get('coverage_pct'), '%')}。")
        lines.append("- MA20 / MA50 / MA200 以上占比：" + " / ".join(
            value(breadth.get(f"pct_above_ma{period}"), "%") for period in (20, 50, 200)))
        if breadth["as_of"] != context.get("report_date"):
            lines.append("- 宽度用于历史背景，本交易日宽度尚未发布。")
    core = [row for row in quotes if row.get("group") == "core"]
    if core:
        lines.extend(["", "**核心资产与自选股**", ""])
        lines.extend(quote_line(row, report_type, context.get("report_date")) for row in core)
    if report_type == "post_market_summary":
        lines.extend(["", "**与开盘报告对比**", "", *morning_comparison(context, context.get("morning_snapshot"))])
    return lines


def _event_value(event: dict) -> str:
    return " / ".join(f"{label} {value(event[key])}" for key, label in (("actual", "实际"), ("estimate", "预期"))
                      if number(event.get(key)) is not None)


def grouped_events(items: list[dict]) -> list[str]:
    """Deduplicate exact rows and group same-time variants, not separate events."""
    groups: dict[tuple, list[dict]] = {}
    seen = set()
    for event in items:
        identity = tuple(str(event.get(key)) for key in ("kind", "date", "name", "period", "comparison", "actual", "estimate", "session"))
        if identity in seen:
            continue
        seen.add(identity)
        name = compact(event.get("name"))
        projection = re.fullmatch(r"Interest Rate Projection\s*-\s*(.+)", name, flags=re.I)
        family = "美联储利率预测" if projection else name
        key = (event.get("kind"), compact(event.get("date")), family, compact(event.get("period")))
        groups.setdefault(key, []).append({**event, "variant": projection[1] if projection else compact(event.get("comparison"))})
    lines = []
    for (kind, date, family, period), rows in sorted(groups.items(), key=lambda item: item[0][1]):
        if kind == "earnings":
            timings = dict.fromkeys({"BeforeMarket": "盘前", "AfterMarket": "盘后"}.get(row.get("session"), "时段待确认") for row in rows)
            lines.append(f"- {date} {family} 财报（{' / '.join(timings)}）。")
            continue
        details = []
        for row in rows:
            item = " ".join(part for part in (row["variant"], _event_value(row)) if part)
            if item and item not in details:
                details.append(item)
        label = " ".join(part for part in (date, family, period) if part)
        lines.append(f"- {label}" + ("：" + "；".join(details) if details else "") + "。")
    return lines


def render_events_and_quality(context: dict) -> list[str]:
    lines = []
    events = context.get("events")
    if events and not events.get("disabled") and (events.get("economic_available") or events.get("earnings_available")):
        lines.extend(["", "**今明交易日关注**", ""])
        lines.append(f"窗口 {events['from']} 至 {events['to']}；{compact(events.get('timezone_note'))}")
        grouped = grouped_events(events.get("items", []))
        lines.extend(grouped[:12])
        if not events.get("items"):
            lines.append("- 本次可用日历中未发现匹配的重要美国经济事件或观察池财报。")
        if grouped:
            lines.append(f"- 已保存 {len(events.get('items', []))}/{events.get('total', len(events.get('items', [])))} 条，合并为 {len(grouped)} 组，显示 {min(12, len(grouped))} 组；数值缺失时不列出，并非零。")
    lines.extend(["", "**数据口径与覆盖**", "",
                  "- 行情为延迟观察，非实时或正式收盘价；各市场日期不同，涨跌相对供应商前收盘（日线回退按所列对比日）。",
                  "- ETF报价代表基金价格，商品期货ETF含展期等影响；债券ETF价格变化不等于信用利差变化。",
                  "- 颜色仅表示数值方向，不代表利好利空或行情新鲜度；新闻与涨跌的因果关系未经验证。"])
    lines.append("- 来源：[EODHD延迟行情](https://eodhd.com/financial-apis/live-ohlcv-stocks-api) · "
                 "[日线](https://eodhd.com/financial-apis/historical-eod-data-api) · "
                 "[美国财政部](https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve) · "
                 "[经济日历](https://eodhd.com/financial-apis/economic-events-data-api) · "
                 "[财报日历](https://eodhd.com/financial-apis/calendar-upcoming-earnings-ipos-and-splits)")
    return lines
