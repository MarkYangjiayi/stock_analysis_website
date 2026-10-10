"""Feishu Card 2.0 layouts for the scheduled reports.

Cards are assembled from the saved structured evidence, never by re-parsing
the audit Markdown, so the same evidence always yields the same card. The
first screen answers "what happened" (rule-based headline, key numbers,
sector ranking, the watch list); full quotes stay in a collapsed panel.
Colour encodes direction only: green up, red down, grey flat/unknown/stale.
Data-quality text appears only when something is actually missing.
"""
from __future__ import annotations

from datetime import date, datetime
import math
import re
from typing import Any
from zoneinfo import ZoneInfo

from services.report_insights import SECTOR_SHORT, current, display_symbol, pct, pre_open, ranked_sectors
from services.report_market import NY, number
from services.report_renderer import _compatible, breadth_notice, compact, instant, morning_moves, value
from services.watchlist_signals import signal_tags


# The Feishu group bot only accepts messages containing its custom keyword
# (code 19024 otherwise); it is matched in the card header/summary.
BRAND = "Quantify"
BEIJING = ZoneInfo("Asia/Shanghai")
WEEKDAYS = "一二三四五六日"
BAR_BLOCKS = 8
QUADRANTS = (("leading", "领先", "green"), ("weakening", "转弱", "orange"),
             ("lagging", "落后", "red"), ("improving", "改善", "blue"))
DETAIL_GROUPS = (("equity", "全球股市"), ("fx", "外汇"), ("commodity", "商品（ETF代理）"),
                 ("credit", "债券价格（ETF代理）"), ("risk", "波动率与加密资产"))
# Headline releases worth a line on the card; speeches, regional surveys and
# sub-series stay in the audit Markdown only.
KEY_EVENTS = tuple((re.compile(pattern, re.I), label) for pattern, label in (
    (r"^core inflation rate\b|^core cpi$", "核心CPI"), (r"^inflation rate\b|^cpi$", "CPI"),
    (r"^core pce price index\b", "核心PCE"), (r"^pce price index\b", "PCE"),
    (r"^non ?farm payrolls\b", "非农就业"), (r"^unemployment rate\b", "失业率"),
    (r"^(fed )?interest rate decision\b", "美联储利率决议"), (r"^fomc minutes\b", "FOMC纪要"),
    (r"^gdp growth rate\b", "GDP"), (r"^retail sales$", "零售销售"),
    (r"^ism manufacturing pmi\b", "ISM制造业"), (r"^ism services pmi\b", "ISM服务业"),
    (r"^initial jobless claims\b", "初请失业金"),
))
FOOTNOTE = "颜色仅表示涨跌方向；新闻为原标题线索，不代表涨跌原因；不构成投资建议。"
WEEKLY_FOOTNOTE = "颜色仅表示涨跌方向；结论按固定规则由数据生成，不构成投资建议。"


def tone(move: Any) -> str:
    move = number(move)
    if move is None or move == 0:
        return "grey"
    return "green" if move > 0 else "red"


def _font(text: str, color: str) -> str:
    return f"<font color='{color}'>{text}</font>"


def _paint(move: Any, text: str | None = None) -> str:
    return _font(text or pct(move), tone(move))


def _md(content: str, size: str = "normal", **extra: Any) -> dict:
    return {"tag": "markdown", "content": content, "text_size": size, **extra}


def _level(price: Any, digits: int = 2) -> str:
    price = number(price)
    if price is None:
        return "—"
    return f"{price:,.0f}" if price >= 10_000 else f"{price:,.{digits}f}"


def _day(raw: str | None) -> str:
    try:
        return date.fromisoformat(str(raw)[:10]).strftime("%m-%d")
    except ValueError:
        return "日期未知"


def _template(move: float | None, threshold: float) -> str:
    if move is None or abs(move) < threshold:
        return "grey"
    return "green" if move > 0 else "red"


def _header(title: str, subtitle: str, template: str, tag: str | None = None) -> dict:
    header = {"title": {"tag": "plain_text", "content": title},
              "subtitle": {"tag": "plain_text", "content": subtitle},
              "template": template, "icon": {"tag": "standard_icon", "token": "calendar_outlined"}}
    if tag:
        header["text_tag_list"] = [{"tag": "text_tag", "text": {"tag": "plain_text", "content": tag}, "color": "neutral"}]
    return header


def _card(header: dict, summary: str, elements: list[dict]) -> dict:
    return {
        "schema": "2.0",
        "config": {"update_multi": True, "width_mode": "default", "summary": {"content": summary[:120]}},
        "header": header,
        "body": {"direction": "vertical", "padding": "12px", "vertical_spacing": "12px", "elements": elements},
    }


def _headline_block(headline: str) -> dict:
    return {"tag": "column_set", "flex_mode": "none", "columns": [{
        "tag": "column", "width": "weighted", "weight": 1, "background_style": "grey-50",
        "padding": "12px", "elements": [_md(headline)],
    }]}


def _cell(label: str, move: Any, *, text: str | None = None, muted: bool = False) -> dict:
    shown = text or (pct(move) if number(move) is not None else "—")
    figure = _font(shown, "grey") if muted or number(move) is None else _paint(move, shown)
    return {"tag": "column", "width": "weighted", "weight": 1, "background_style": "grey-50",
            "padding": "8px", "vertical_spacing": "2px",
            "elements": [_md(_font(label, "grey"), "notation"), _md(figure, "heading-4")]}


def _quote_cell(row: dict | None, label: str, show_level: bool = False) -> dict:
    if not row or number(row.get("price")) is None:
        return _cell(label, None)
    if show_level:
        label = f"{label} {_level(row['price'])}"
    if row.get("session", {}).get("state") == "未开盘":
        label += " · 前日"
    if not row.get("fresh"):
        return _cell(f"{label} · 旧", row.get("change_pct"), muted=True)
    return _cell(label, row.get("change_pct"))


def _grid(cells: list[dict]) -> list[dict]:
    # bisect: four across on desktop, two by two on a phone.
    return [{"tag": "column_set", "flex_mode": "bisect", "horizontal_spacing": "8px", "columns": cells[i:i + 4]}
            for i in range(0, len(cells), 4)]


def _bar_line(label: str, move: float, scale: float) -> str:
    blocks = round(abs(move) / scale * BAR_BLOCKS) if scale else 0
    bar = "▇" * max(blocks, 1) if abs(move) >= 0.005 else "·"
    return f"{label} {_font(bar, tone(move))} {_paint(move)}"


def _sector_bars(title: str, rows: list[dict], note: str = "") -> list[dict]:
    """Ranked bars in two columns; 'stretch' stacks them in order on a phone."""
    if not rows:
        return []
    scale = max(abs(row["change_pct"]) for row in rows)
    lines = [_bar_line(SECTOR_SHORT.get(row["ticker"], row.get("name", row["ticker"])), row["change_pct"], scale)
             for row in rows]
    half = math.ceil(len(lines) / 2)
    columns = [{"tag": "column", "width": "weighted", "weight": 1, "elements": [_md("\n".join(chunk))]}
               for chunk in (lines[:half], lines[half:]) if chunk]
    return [_md(f"**{title}**" + (f"　{note}" if note else "")),
            {"tag": "column_set", "flex_mode": "stretch", "horizontal_spacing": "12px", "columns": columns}]


def _symbol_cell(row: dict, site_url: str) -> str:
    symbol = display_symbol(row)
    name = row.get("name") or ""
    label = f"[{symbol}]({site_url}/?ticker={row['ticker']})" if site_url else f"**{symbol}**"
    return label + (f" {name}" if name and name not in (row.get("ticker"), symbol) and not symbol.startswith(name) else "")


def _earnings_tags(ticker: str, events: dict | None) -> list[dict]:
    sessions = {"BeforeMarket": "盘前", "AfterMarket": "盘后"}
    return [{"text": f"财报 {_day(item.get('date'))} {sessions.get(item.get('session'), '')}".strip(), "color": "violet"}
            for item in (events or {}).get("items", []) if item.get("kind") == "earnings" and item.get("name") == ticker][:1]


def _watch_table(rows: list[dict], *, signals: dict | None, events: dict | None, site_url: str,
                 change_label: str) -> dict | None:
    """Root-level table (Feishu forbids nesting tables in panels or columns)."""
    if not rows:
        return None

    def order(row: dict) -> tuple:
        move = number(row.get("change_pct"))
        return (move is None, not row.get("fresh", True), -abs(move or 0))

    table_rows = []
    for row in sorted(rows, key=order):
        move = number(row.get("change_pct"))
        if move is None:
            change = [{"text": "—", "color": "neutral"}]
        elif not row.get("fresh", True):
            change = [{"text": f"{pct(move)} 旧", "color": "neutral"}]
        else:
            change = [{"text": pct(move), "color": {"grey": "neutral"}.get(tone(move), tone(move))}]
        currency = row.get("currency") or "USD"
        price = _level(row.get("price"))
        table_rows.append({
            "symbol": _symbol_cell(row, site_url),
            "price": price if currency == "USD" or price == "—" else f"{price} {currency}",
            "change": change,
            "signals": signal_tags(row["ticker"], signals) + _earnings_tags(row["ticker"], events),
        })
    return {
        "tag": "table", "page_size": min(10, len(table_rows)), "row_height": "low", "freeze_first_column": True,
        "header_style": {"background_style": "grey", "text_color": "grey", "bold": True, "text_size": "normal"},
        "columns": [
            {"name": "symbol", "display_name": "标的", "data_type": "lark_md", "width": "auto"},
            {"name": "price", "display_name": "价格", "data_type": "text", "horizontal_align": "right", "width": "auto"},
            {"name": "change", "display_name": change_label, "data_type": "options", "width": "auto"},
            {"name": "signals", "display_name": "信号", "data_type": "options", "width": "auto"},
        ],
        "rows": table_rows,
    }


def _panel(title: str, contents: list[str]) -> dict:
    return {
        "tag": "collapsible_panel", "expanded": False,
        "header": {"title": {"tag": "plain_text", "content": title}},
        "border": {"color": "grey", "corner_radius": "8px"},
        "padding": "8px", "vertical_spacing": "8px",
        "elements": [_md(content) for content in contents if content],
    }


def _buttons(site_url: str, links: tuple[tuple[str, str], ...]) -> list[dict]:
    if not site_url:
        return []
    return [{"tag": "column_set", "flex_mode": "none", "horizontal_spacing": "8px", "columns": [
        {"tag": "column", "width": "weighted", "weight": 1, "elements": [{
            "tag": "button", "text": {"tag": "plain_text", "content": label}, "type": "default",
            "width": "fill", "size": "medium",
            "behaviors": [{"type": "open_url", "default_url": site_url + path}],
        }]} for label, path in links]}]


def _calendar_block(title: str, events: dict | None, names: dict[str, str], limit: int) -> dict | None:
    """One line per day: key releases, then watch-list earnings; the rest is counted."""
    days: dict[str, tuple[list[str], list[str]]] = {}
    minor = 0
    sessions = {"BeforeMarket": "盘前", "AfterMarket": "盘后"}
    for item in sorted((events or {}).get("items", []), key=lambda event: str(event.get("date"))):
        day = str(item.get("date"))[:10]
        macro, earnings = days.setdefault(day, ([], []))
        if item.get("kind") == "earnings":
            ticker = str(item.get("name"))
            name = names.get(ticker) or display_symbol({"ticker": ticker})
            timing = sessions.get(item.get("session"))
            label, target = f"{name} 财报" + (f"（{timing}）" if timing else ""), earnings
        else:
            label = next((label for pattern, label in KEY_EVENTS if pattern.search(compact(item.get("name")))), None)
            target = macro
            if label is None:
                minor += 1
                continue
        if label not in target:
            target.append(label)
    lines = []
    for day, labels in [(day, macro + earnings) for day, (macro, earnings) in sorted(days.items()) if macro or earnings][:limit]:
        try:
            weekday = f" 周{WEEKDAYS[date.fromisoformat(day).weekday()]}"
        except ValueError:
            weekday = ""
        lines.append(f"- {_day(day)}{weekday}：{' · '.join(labels)}")
    if not lines:
        return None
    if minor:
        lines.append(_font(f"另有 {minor} 项次要数据或官员讲话；日期按供应商原文", "grey"))
    return _md(f"**{title}**\n" + "\n".join(lines))


def _footer(parts: list[str], issues: list[str], note: str = FOOTNOTE) -> dict:
    text = " · ".join(parts)
    if issues:
        text += "\n⚠ " + "；".join(dict.fromkeys(issues))
    return _md(f"{text}\n{note}", "notation")


# --- daily -----------------------------------------------------------------


def _detail_line(row: dict, report_date: str | None) -> str:
    name, symbol = row.get("name") or row["ticker"], display_symbol(row)
    label = f"{name} {_font(symbol, 'grey')}" if row.get("proxy") or row.get("group") == "core" else name
    if number(row.get("price")) is None:
        return f"- {label} {_font('数据不可用', 'grey')}"
    price = _level(row["price"], 4 if row.get("group") == "fx" else 2)
    move = number(row.get("change_pct"))
    if not row.get("fresh"):
        figure = _font(f"{pct(move) if move is not None else '涨跌未知'} · {row.get('status_label') or '旧报价'}", "grey")
    else:
        figure = _paint(move) if move is not None else _font("涨跌未知", "grey")
    when = f" {_font(_day(row.get('session_date')), 'grey')}" if row.get("fresh") and row.get("session_date") not in (None, report_date) else ""
    return f"- {label} {price} {figure}{when}"


def _detail_sections(context: dict, report_type: str) -> list[str]:
    quotes, report_date = context.get("quotes", []), context.get("report_date")
    sections = []
    for group, title in DETAIL_GROUPS:
        rows = [row for row in quotes if row.get("group") == group]
        if rows:
            sections.append(f"**{title}**\n" + "\n".join(_detail_line(row, report_date) for row in rows))
    treasury = context.get("treasury")
    if treasury:
        tenors = " · ".join(f"{row['tenor'].upper()} {value(row.get('yield_pct'), '%')} {_paint(row.get('change_bp'), value(row.get('change_bp'), 'bp', True, 0))}"
                            for row in treasury.get("values", []))
        spread = value(treasury.get("spread_10y_2y_bp"), "bp", True, 0)
        sections.append(f"**美债收益率** {_font(_day(treasury.get('as_of')) + ' 官方日频', 'grey')}\n"
                        f"- {tenors}\n- 10Y−2Y {spread}（日变化 {_paint(treasury.get('spread_change_bp'), value(treasury.get('spread_change_bp'), 'bp', True, 0))}）")
    breadth = context.get("breadth")
    if breadth and breadth_notice(context) is None:
        sections.append(
            f"**标普500 市场宽度** {_font(_day(breadth['as_of']) + ' 已发布日线', 'grey')}\n"
            f"- 涨 {breadth['advances']} / 跌 {breadth['declines']} · 站上 MA20 / MA50 / MA200："
            + " / ".join(value(breadth.get(f"pct_above_ma{period}"), "%", digits=0) for period in (20, 50, 200)))
    if report_type == "post_market_summary":
        moves = morning_moves(context, context.get("morning_snapshot"))
        if moves:
            sections.append("**自早报以来**\n" + "\n".join(f"- {row['name']} {_paint(move)}" for _, row, move in moves))
    return sections


def _anomaly_panel(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    lines = []
    for row in rows:
        status = f" {_font('· ' + row['status'], 'grey')}" if row.get("status") else ""
        company = f" {row['company']}" if row.get("company") else ""
        head = f"**{row['ticker']}**{company} {_paint(row.get('move')) if number(row.get('move')) is not None else _font('涨跌未知', 'grey')}{status}"
        news = row.get("headlines") or []
        detail = "；".join(f"[{title}]({link})" for title, link in news) if news else _font(row.get("news_note") or "暂无相关新闻", "grey")
        lines.append(f"- {head}\n  {detail}")
    lines.append(_font("新闻为原标题线索，尚未验证为涨跌原因。", "grey"))
    preview = " · ".join(f"{row['ticker']} {pct(row.get('move'), 1)}" for row in rows[:3] if number(row.get("move")) is not None)
    title = f"个股异动 · {preview}" + (f" 等 {len(rows)} 只" if len(rows) > 3 else "")
    return _panel(title, ["\n".join(lines)])


def _daily_issues(context: dict, anomaly_rows: list[dict]) -> list[str]:
    quotes = context.get("quotes", [])
    issues = []
    missing = [row.get("name") or row["ticker"] for row in quotes if number(row.get("price")) is None]
    if missing:
        issues.append(f"缺失 {len(missing)} 项：{'、'.join(missing[:4])}{'等' if len(missing) > 4 else ''}")
    stale = sum(number(row.get("price")) is not None and not row.get("fresh") for row in quotes)
    if stale:
        issues.append(f"{stale} 项为旧报价（已置灰）")
    gaps = [name for name, present in (("美债曲线", context.get("treasury")), ("市场宽度", context.get("breadth"))) if not present]
    if events := context.get("events"):
        if not events.get("disabled") and not (events.get("economic_available") and events.get("earnings_available")):
            gaps.append("事件日历")
    elif events is None:
        gaps.append("事件日历")
    if gaps:
        issues.append("暂缺：" + "、".join(gaps))
    if notice := breadth_notice(context):
        issues.append(notice.rstrip("。"))
    covered = {f"{name}暂不可用。" for name in ("跨资产行情", "美债曲线", "市场宽度", "事件日历")}
    issues.extend(str(item).rstrip("。") for item in dict.fromkeys(context.get("warnings", [])) if item not in covered)
    stale_movers = sum(bool(row.get("status")) for row in anomaly_rows)
    if stale_movers:
        issues.append(f"异动股 {stale_movers}/{len(anomaly_rows)} 为非当期报价")
    signals = context.get("watchlist_signals") or {}
    if signals.get("error") or signals.get("refresh_failures"):
        issues.append("部分关注列表信号不可用")
    return issues


def build_daily_card(context: dict, anomaly_rows: list[dict], *, report_type: str, headline: str,
                     site_url: str = "", tag: str | None = None) -> dict:
    quotes = context.get("quotes", [])
    by_symbol = {row.get("ticker"): row for row in quotes}
    report_date = context.get("report_date")
    captured = instant(context.get("captured_at"))
    day = date.fromisoformat(report_date) if report_date else (captured or datetime.now(NY)).date()
    kind = "开盘" if report_type == "morning_briefing" else "收盘"
    title = f"{BRAND} 美股{kind} · {day.month}月{day.day}日 周{WEEKDAYS[day.weekday()]}"
    when = (f"北京 {captured.astimezone(BEIJING):%m-%d %H:%M} · 纽约 {captured.astimezone(NY):%H:%M} 采集"
            if captured else "采集时间未知")
    header = _header(title, f"{when} · 延迟行情", _template(current(by_symbol.get("SPY.US")), 0.5), tag)

    treasury = context.get("treasury") or {}
    ten = next((row for row in treasury.get("values", []) if row.get("tenor") == "10y"), None)
    if ten and number(ten.get("yield_pct")) is not None:
        label = f"10Y 美债 {value(ten['yield_pct'], '%')}" + ("" if treasury.get("as_of") == report_date else f" · {_day(treasury.get('as_of'))}")
        rate = _cell(label, ten.get("change_bp"), text=value(ten.get("change_bp"), "bp", True, 0))
    else:
        rate = _cell("10Y 美债", None)
    cells = [_quote_cell(by_symbol.get("SPY.US"), "标普500"), _quote_cell(by_symbol.get("QQQ.US"), "纳指100"),
             _quote_cell(by_symbol.get("IWM.US"), "罗素2000"), _quote_cell(by_symbol.get("VIX.INDX"), "VIX", True),
             rate, _quote_cell(by_symbol.get("NYICDX.INDX"), "美元指数", True),
             _quote_cell(by_symbol.get("GLD.US"), "黄金 GLD"), _quote_cell(by_symbol.get("BTC-USD.CC"), "比特币", True)]

    rsp, spy = by_symbol.get("RSP.US"), by_symbol.get("SPY.US")
    note = ""
    if _compatible(rsp, spy) and current(rsp) is not None and current(spy) is not None:
        relative = ((1 + rsp["change_pct"] / 100) / (1 + spy["change_pct"] / 100) - 1) * 100
        note = f"等权相对市值加权 {_paint(relative)}"

    sector_title = "板块 · 前一交易日" if pre_open(by_symbol) else "板块"
    elements = [_headline_block(headline), *_grid(cells), *_sector_bars(sector_title, ranked_sectors(quotes), note)]
    table = _watch_table([row for row in quotes if row.get("group") == "core"],
                         signals=context.get("watchlist_signals"), events=context.get("events"),
                         site_url=site_url, change_label="涨跌")
    if table:
        elements.extend([_md("**关注列表**"), table])
    names = {row["ticker"]: row.get("name") for row in quotes if row.get("group") == "core"}
    if calendar := _calendar_block("今明关注", context.get("events"), names, limit=2):
        elements.append(calendar)
    if panel := _anomaly_panel(anomaly_rows):
        elements.append(panel)
    if details := _detail_sections(context, report_type):
        elements.append(_panel("全部行情明细 · 全球股市 / 外汇 / 商品 / 债券 / 加密", details))
    available = sum(number(row.get("price")) is not None and bool(row.get("fresh")) for row in quotes)
    elements.append(_footer([f"行情 {available}/{len(quotes)} 当期可用", "EODHD 延迟报价，非正式收盘价"],
                            _daily_issues(context, anomaly_rows)))
    elements.extend(_buttons(site_url, (("市场总览", "/market"), ("异动扫描", "/anomalies"))))
    return _card(header, f"{title}：{headline}", elements)


# --- weekly ----------------------------------------------------------------


def _rrg_lines(rrg: dict) -> list[str]:
    lines = []
    for key, label, color in QUADRANTS:
        members = [item for item in rrg.get("items", []) if item.get("quadrant") == key]
        if not members:
            continue
        names = "、".join(SECTOR_SHORT.get(item["ticker"], item["ticker"])
                         + ("（新进）" if item.get("previous_quadrant") not in (None, key) else "") for item in members)
        lines.append(f"<text_tag color='{color}'>{label}</text_tag> {names}")
    return lines


def _temperature_lines(digest: dict) -> list[str]:
    lines = []
    breadth = digest.get("breadth")
    if breadth:
        parts = []
        for period in (50, 200):
            now, before = number(breadth.get(f"pct_above_ma{period}")), number(breadth.get(f"previous_pct_above_ma{period}"))
            if now is None:
                continue
            change = f"（{_paint(now - before, value(now - before, 'pt', True, 0))}）" if before is not None else ""
            parts.append(f"站上 MA{period} {value(now, '%', digits=0)}{change}")
        if parts:
            lines.append("- " + " · ".join(parts))
        highs, lows = breadth.get("new_highs"), breadth.get("new_lows")
        if highs is not None and lows is not None:
            net = breadth.get("net_advances")
            lines.append(f"- {_day(breadth.get('as_of'))} 52周新高 {highs} / 新低 {lows}"
                         + (f" · 本周涨跌家数差 {_paint(net, f'{net:+,}')}" if net is not None else ""))
    valuation = digest.get("valuation")
    if valuation and number(valuation.get("index_pe")) is not None:
        rank = f"，{valuation['months']} 个月历史 {value(valuation.get('percentile'), '%', digits=0)} 分位" if valuation.get("percentile") is not None else ""
        lines.append(f"- 标普500 P/E {value(valuation['index_pe'], digits=1)}{rank} {_font(_day(valuation.get('as_of')) + ' 月末估算', 'grey')}")
    return lines


def _weekly_issues(digest: dict) -> list[str]:
    issues = [str(item).rstrip("。") for item in digest.get("warnings", [])]
    for key, label in (("rrg", "板块轮动"), ("breadth", "市场宽度"), ("valuation", "指数估值")):
        if not digest.get(key):
            issues.append(f"{label}暂不可用")
    signals = digest.get("watchlist_signals") or {}
    if signals.get("error") or signals.get("refresh_failures"):
        issues.append("部分关注列表信号不可用")
    return issues


def build_weekly_card(digest: dict, *, headline: str, site_url: str = "", tag: str | None = None) -> dict:
    start, end = date.fromisoformat(digest["week_start"]), date.fromisoformat(digest["week_end"])
    title = f"{BRAND} 美股周报 · {start.month}月{start.day}日–{end.month}月{end.day}日"
    by_symbol = {row["ticker"]: row for row in digest.get("assets", [])}
    spy = by_symbol.get("SPY.US", {})
    header = _header(title, f"截至 {end.month}/{end.day} 收盘 · 周涨跌相对上周五收盘",
                     _template(number(spy.get("change_pct")), 1.0), tag)

    def cell(ticker: str, label: str, show_level: bool = False) -> dict:
        row = by_symbol.get(ticker)
        return _quote_cell({**row, "fresh": row.get("as_of") == digest["week_end"] or ticker.endswith(".CC")} if row else None,
                           label, show_level)

    cells = [cell("SPY.US", "标普500"), cell("QQQ.US", "纳指100"), cell("IWM.US", "罗素2000"), cell("VIX.INDX", "VIX", True),
             cell("GLD.US", "黄金 GLD"), cell("TLT.US", "长期美债 TLT"), cell("NYICDX.INDX", "美元指数", True),
             cell("BTC-USD.CC", "比特币", True)]
    sectors = sorted((row for row in digest.get("sectors", []) if number(row.get("change_pct")) is not None),
                     key=lambda row: row["change_pct"], reverse=True)
    note = f"等权相对市值加权 {_paint(digest['rsp_relative_pct'])}" if number(digest.get("rsp_relative_pct")) is not None else ""
    elements = [_headline_block(headline), *_grid(cells), *_sector_bars("板块周涨跌", sectors, note)]
    rrg = digest.get("rrg")
    if rrg and (lines := _rrg_lines(rrg)):
        elements.append(_md(f"**板块轮动** {_font('RRG 相对标普，截至 ' + _day(rrg.get('as_of')), 'grey')}\n" + "\n".join(lines)))
    if lines := _temperature_lines(digest):
        elements.append(_md("**市场温度**\n" + "\n".join(lines)))
    table = _watch_table([{**row, "fresh": True} for row in digest.get("watchlist", [])],
                         signals=digest.get("watchlist_signals"), events=digest.get("events"),
                         site_url=site_url, change_label="周涨跌")
    if table:
        elements.extend([_md("**关注列表**"), table])
    names = {row["ticker"]: row.get("name") for row in digest.get("watchlist", [])}
    if calendar := _calendar_block("下周关注", digest.get("events"), names, limit=5):
        elements.append(calendar)
    elements.append(_footer(["数据来自已发布的日线流水线与 EODHD 日线", "宽度与估值口径同网站"], _weekly_issues(digest),
                            WEEKLY_FOOTNOTE))
    elements.extend(_buttons(site_url, (("板块轮动", "/rrg"), ("指数估值", "/market/index-valuation"))))
    return _card(header, f"{title}：{headline}", elements)
