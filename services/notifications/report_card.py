"""Read-only Card 2.0 layout for the audited daily-report Markdown.

No data is re-fetched or re-summarized by an LLM. Long sections are client-side
collapsible panels; an empty-stock notice is inlined instead of an empty panel.
Generic notifications retain their old layout.
"""
from __future__ import annotations

import re


_CHANGE = re.compile(r"(?<![\w.+-])(?:[+-][\d,]+\.\d{2}(?:%| bp| 点)|微[升降]（不足 0\.01(?:%| bp| 点)）)")
_MARKET_SECTIONS = {
    "核心变化", "全球股票市场", "外汇", "商品代理", "贵金属（ETF代理）",
    "能源与工业金属（ETF代理）", "债券价格代理", "波动率与加密资产",
    "波动率", "加密资产（BTC / ETH）", "板块与市场宽度", "核心资产与自选股", "与开盘报告对比",
}


def _paint_changes(text: str) -> str:
    def paint(match: re.Match) -> str:
        value = match[0]
        if value.startswith("微"):
            color = "green" if value.startswith("微升") else "red"
        else:
            amount = float(value.removesuffix("%").removesuffix(" bp").removesuffix(" 点").replace(",", ""))
            color = "green" if amount > 0 else "red" if amount < 0 else "grey"
        return f"<font color='{color}'>{value}</font>"
    return _CHANGE.sub(paint, text)


def _color_section(heading: str, body: str) -> str:
    """Color generated market moves only, never headlines, links or rate levels.

    The database audit text stays plain Markdown. Unknown/free-form sections
    fail closed to unchanged text instead of guessing which numbers are moves.
    """
    lines = []
    for line in body.splitlines():
        if heading == "个股异动与新闻线索":
            stock = re.fullmatch(r"(- \*\*[A-Z0-9.^/_-]+ )(.+?)(\*\* · .*)", line)
            if stock:
                line = stock[1] + _paint_changes(stock[2]) + stock[3]
        elif line.startswith("- ") and not any(char in line for char in "[]`<>"):
            if heading in _MARKET_SECTIONS:
                line = _paint_changes(line)
            elif heading == "美债收益率（日频）":
                # A positive yield/spread level is not an upward change.
                separator = "；变化 " if line.startswith("- 10Y−2Y：") else " / "
                before, found, change = line.partition(separator)
                if found:
                    line = before + found + _paint_changes(change)
        lines.append(line)
    return "\n".join(lines)


def _markdown(content: str, *, caption: bool = False) -> dict:
    return {"tag": "markdown", "content": content, "text_size": "notation" if caption else "normal"}


def _panel(title: str, contents: list[str]) -> dict:
    return {
        "tag": "collapsible_panel", "expanded": False,
        "header": {"title": {"tag": "plain_text", "content": title}},
        "border": {"color": "grey", "corner_radius": "8px"},
        "padding": "8px", "vertical_spacing": "8px",
        "elements": [_markdown(content) for content in contents if content],
    }


def build_daily_report_card(title: str, content: str) -> dict:
    sections: list[tuple[str, str]] = []
    heading, lines = "", []
    for line in content.splitlines():
        match = re.fullmatch(r"\*\*([^*]+)\*\*", line)
        if match:
            if heading or lines:
                sections.append((heading, "\n".join(lines).strip()))
            heading, lines = match[1], []
        else:
            lines.append(line)
    sections.append((heading, "\n".join(lines).strip()))

    summary, notices, markets, stocks, followup = [], [], [], [], []
    subtitle = "延迟行情 · 明细可展开"
    for heading, body in sections:
        body = _color_section(heading, body)
        full = f"**{heading}**\n{body}" if heading else body
        if "｜" in heading and heading.startswith(("美股开盘速递", "美股盘后总结")):
            subtitle = heading + " · " + body.split("；", 1)[0]
        elif heading == "核心变化":
            summary.append(full)
        elif heading == "数据提示" or not heading or heading.startswith("测试"):
            if full:
                notices.append(full)
        elif heading == "个股异动与新闻线索":
            if body == "- 本次扫描没有可展示的个股异动；不据此判断整体市场平稳。":
                notices.append("- 个股异动：无可展示结果，不代表市场平稳。")
            else:
                stocks.append(full)
        elif heading in {"与开盘报告对比", "今明交易日关注", "数据口径与覆盖"}:
            followup.append(full)
        else:
            markets.append(full)

    # At most five blocks: one focal summary, quality, and non-empty panels.
    elements = [{
        "tag": "column_set", "flex_mode": "none",
        "columns": [{
            "tag": "column", "width": "weighted", "weight": 1,
            "background_style": "grey-50", "padding": "12px", "vertical_spacing": "4px",
            "elements": [_markdown(item) for item in summary] or [_markdown("**核心变化**\n有效行情不足，请查看数据提示。")],
        }],
    }]
    legend = "<font color='green'>上涨 +</font> / <font color='red'>下跌 −</font>；平盘／未知不着色。"
    elements.append(_markdown(("\n\n".join(notices) or "逐项报价时间、口径与缺失状态见展开明细。") + "\n" + legend, caption=True))
    if markets:
        elements.append(_panel("展开行情明细 · 贵金属 / BTC / 全球资产", markets))
    if stocks:
        elements.append(_panel("展开个股异动与新闻线索 · 含逐项报价时间", stocks))
    if followup:
        elements.append(_panel("展开早晚对比 / 事件日历 / 数据口径", followup))
    return {
        "schema": "2.0",
        "config": {"update_multi": True, "width_mode": "default", "summary": {"content": title}},
        "header": {"title": {"tag": "plain_text", "content": title.lstrip("🌅🌃🧪 ")},
                   "subtitle": {"tag": "plain_text", "content": subtitle}, "template": "grey",
                   "icon": {"tag": "standard_icon", "token": "calendar_outlined"}},
        "body": {"direction": "vertical", "padding": "12px", "vertical_spacing": "12px", "elements": elements},
    }
