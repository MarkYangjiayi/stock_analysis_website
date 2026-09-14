"""Read-only Card 2.0 layout for the audited daily-report Markdown.

No data is re-fetched, re-summarized by an LLM, or discarded. Long sections are
client-side collapsible panels; generic notifications retain their old layout.
"""
from __future__ import annotations

import re


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
        full = f"**{heading}**\n{body}" if heading else body
        if "｜" in heading and heading.startswith(("美股开盘速递", "美股盘后总结")):
            subtitle = heading + " · " + body.split("；", 1)[0]
        elif heading == "核心变化":
            summary.append(full)
        elif heading == "数据提示" or not heading or heading.startswith("测试"):
            if full:
                notices.append(full)
        elif heading == "个股异动与新闻线索":
            stocks.append(full)
        elif heading in {"与开盘报告对比", "今明交易日关注", "数据口径与覆盖"}:
            followup.append(full)
        else:
            markets.append(full)

    # Five top-level blocks: one focal summary, upfront quality, three panels.
    elements = [{
        "tag": "column_set", "flex_mode": "none",
        "columns": [{
            "tag": "column", "width": "weighted", "weight": 1,
            "background_style": "blue-50", "padding": "12px", "vertical_spacing": "4px",
            "elements": [_markdown(item) for item in summary] or [_markdown("**核心变化**\n有效行情不足，请查看数据提示。")],
        }],
    }]
    elements.append(_markdown("\n\n".join(notices) or "逐项报价时间、口径与缺失状态见展开明细。", caption=True))
    if markets:
        elements.append(_panel("展开行情明细 · 全球资产 / 板块 / 核心股票", markets))
    if stocks:
        elements.append(_panel("展开个股异动与新闻线索 · 含逐项报价时间", stocks))
    if followup:
        elements.append(_panel("展开早晚对比 / 事件日历 / 数据口径", followup))
    return {
        "schema": "2.0",
        "config": {"update_multi": True, "width_mode": "default", "summary": {"content": title}},
        "header": {"title": {"tag": "plain_text", "content": title.lstrip("🌅🌃🧪 ")},
                   "subtitle": {"tag": "plain_text", "content": subtitle}, "template": "blue",
                   "icon": {"tag": "standard_icon", "token": "calendar_outlined"}},
        "body": {"direction": "vertical", "padding": "12px", "vertical_spacing": "12px", "elements": elements},
    }
