"""Deterministic market sections using only the saved report snapshot.

Compact lists work in both the full audit text and the foldable Feishu card;
the report does not depend on client-specific table rendering.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.report_market import number


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
        lines.append(f"- 模块缺口：{'、'.join(unavailable)}；行情覆盖不代表报告完整。")
    lines.extend(f"- {compact(warning)}" for warning in dict.fromkeys(context.get("warnings", [])))
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


def quote_line(row: dict, report_type: str) -> str:
    name, ticker = compact(row.get("name")), compact(row.get("ticker"))
    proxy = f"；代理：{compact(row['proxy'])}" if row.get("proxy") else ""
    if row.get("price") is None:
        return f"- {name}（{ticker}{proxy}）：数据不可用"
    basis = "；复权变动" if row.get("basis") == "eod_adjusted" else ""
    if row.get("basis", "").startswith("eod_"):
        basis += f"；对比日 {row.get('previous_date') or '未知'}"
    state = row.get("session", {}).get("state", "状态未知")
    if state == "未开盘" and row.get("fresh"):
        state = "该交易日已收盘／当地新交易日未开盘"
    details = ""
    if row.get("calendar") == "XNYS" and row.get("fresh") and row.get("from_open_pct") is not None:
        label = "开盘以来" if report_type == "morning_briefing" else "较开盘"
        details = f"；{label} {value(row['from_open_pct'], '%', True)}"
    price = value(row.get("price"), digits=4 if row.get("group") == "fx" else 2)
    unit = "点" if ticker.endswith(".INDX") else compact(row.get("currency"))
    if ticker == "VIX.INDX" and number(row.get("previous_close")) is not None:
        details += f"；变动 {value(row['price'] - row['previous_close'], ' 点', True)}"
    return (f"- {name}（{ticker}{proxy}）：{price} {unit} / {value(row.get('change_pct'), '%', True)}"
            f"{details}；{stamp(row)} · {state} · {compact(row.get('status_label'))}{basis}")


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
        if row.get("ticker") not in {"SPY.US", "QQQ.US", "IWM.US", "VIX.INDX"}:
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
    if overseas:
        leader, laggard = max(overseas, key=lambda row: row["change_pct"]), min(overseas, key=lambda row: row["change_pct"])
        lines.append(f"- 海外可用市场中最强：{leader['name']} {value(leader['change_pct'], '%', True)}；"
                     f"最弱：{laggard['name']} {value(laggard['change_pct'], '%', True)}。各市场观察时点见下。")
    else:
        lines.append("- 海外市场当前交易日有效报价不足，不能判断相对强弱。")
    risk = [by_symbol[ticker] for ticker in ("VIX.INDX", "NYICDX.INDX", "GLD.US")
            if ticker in by_symbol and by_symbol[ticker] in fresh]
    lines.append("- 跨资产观察：" + ("；".join(f"{row['name']}{'（ETF代理）' if row.get('proxy') else ''} {value(row['change_pct'], '%', True)}" for row in risk)
                                        if risk else "有效报价不足。"))
    for group, label in (("equity", "全球股票市场"), ("fx", "外汇"), ("commodity", "商品代理"),
                         ("credit", "债券价格代理"), ("risk", "波动率与加密资产")):
        lines.extend(["", f"**{label}**", ""])
        lines.extend(quote_line(row, report_type) for row in quotes if row.get("group") == group)
    lines.extend(["", "**美债收益率（日频）**", ""])
    treasury = context.get("treasury")
    if treasury:
        lines.append(f"观察日 {treasury['as_of']}；变化相对 {treasury.get('previous_date') or '未知日期'}，并非盘中利率。")
        for row in treasury.get("values", []):
            lines.append(f"- {row['tenor'].upper()}：{value(row.get('yield_pct'), '%')} / {value(row.get('change_bp'), ' bp', True)}")
        lines.append(f"- 10Y−2Y：{value(treasury.get('spread_10y_2y_bp'), ' bp', True)}；"
                     f"变化 {value(treasury.get('spread_change_bp'), ' bp', True)}")
        if treasury.get("meta", {}).get("stale"):
            lines.append("- 数据提示：使用较早观察或回退缓存。")
    else:
        lines.append("- 美债曲线暂不可用。")
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
    if breadth:
        lines.append(f"- 市场宽度：截至 {breadth['as_of']} 的已发布日线，{breadth['universe']}；"
                     f"涨 {breadth['advances']} / 跌 {breadth['declines']} / 平 {breadth['unchanged']}；"
                     f"价格覆盖 {value(breadth.get('coverage_pct'), '%')}。")
        lines.append("- MA20 / MA50 / MA200 以上占比：" + " / ".join(
            value(breadth.get(f"pct_above_ma{period}"), "%") for period in (20, 50, 200)))
        if breadth["as_of"] != context.get("report_date"):
            lines.append("- 宽度用于历史背景，本交易日宽度尚未发布。")
    else:
        lines.append("- 已发布市场宽度暂不可用。")
    lines.extend(["", "**核心资产与自选股**", ""])
    lines.extend(quote_line(row, report_type) for row in quotes if row.get("group") == "core")
    if report_type == "post_market_summary":
        lines.extend(["", "**与开盘报告对比**", "", *morning_comparison(context, context.get("morning_snapshot"))])
    return lines


def render_events_and_quality(context: dict) -> list[str]:
    lines = ["", "**今明交易日关注**", ""]
    events = context.get("events")
    if events and events.get("disabled"):
        lines.append("- 事件日历未启用。")
    elif events:
        lines.append(f"窗口 {events['from']} 至 {events['to']}；{compact(events.get('timezone_note'))}")
        for event in events.get("items", []):
            if event["kind"] == "earnings":
                timing = {"BeforeMarket": "盘前", "AfterMarket": "盘后"}.get(event.get("session"), "时段待确认")
                lines.append(f"- {event['date']} {event['name']} 财报（{timing}）。")
            else:
                details = " · ".join(compact(event.get(key)) for key in ("period", "comparison") if event.get(key))
                lines.append(f"- {compact(event['date'])} {compact(event['name'])} {details}；"
                             f"供应商实际值 {value(event.get('actual'))} / 预期 {value(event.get('estimate'))}。")
        if not events.get("items"):
            lines.append("- 本次可用日历中未发现匹配的重要美国经济事件或观察池财报。")
        if events.get("total", 0) > len(events.get("items", [])):
            lines.append(f"- 匹配 {events['total']} 项，正文展示前 {len(events['items'])} 项。")
        if not events.get("economic_available"):
            lines.append("- 美国经济日历不可用，不能据此判断没有事件。")
        if not events.get("earnings_available"):
            lines.append("- 观察池财报日历不可用。")
    else:
        lines.append("- 日历暂不可用，不能据此判断没有重要事件。")
    quotes = context.get("quotes", [])
    current = sum(bool(row.get("fresh")) for row in quotes)
    historical = sum(row.get("price") is not None and not row.get("fresh") for row in quotes)
    lines.extend(["", "**数据口径与覆盖**", "",
                  f"- 当期可用 {current}/{len(quotes)}；历史或待更新 {historical}；缺失 {len(quotes) - current - historical}。",
                  "- 行情为延迟快照；涨跌相对供应商前收盘，日线回退注明复权口径。加密资产不标作24小时涨跌。",
                  "- ETF报价代表基金价格，商品期货ETF含展期等影响；债券ETF价格变化不等于信用利差变化。",
                  "- 每项独立标注观察时间；共同涨跌只描述现象，不自动推断因果。"])
    lines.extend(f"- {compact(warning)}" for warning in context.get("warnings", []))
    lines.append("- 来源：[EODHD延迟行情](https://eodhd.com/financial-apis/live-ohlcv-stocks-api) · "
                 "[日线](https://eodhd.com/financial-apis/historical-eod-data-api) · "
                 "[美国财政部](https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve) · "
                 "[经济日历](https://eodhd.com/financial-apis/economic-events-data-api) · "
                 "[财报日历](https://eodhd.com/financial-apis/calendar-upcoming-earnings-ipos-and-splits)")
    return lines
