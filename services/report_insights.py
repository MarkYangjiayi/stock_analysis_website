"""Rule-based one-line takeaways for the pushed reports.

Every clause is derived from saved evidence with fixed thresholds. No LLM is
involved and no clause asserts a cause: wording only describes what moved and
by how much. A clause whose inputs are missing or stale is dropped rather than
guessed, so a sparse snapshot yields a shorter sentence, never a wrong one.
"""
from __future__ import annotations

from typing import Any

from services.report_market import number
from services.report_renderer import _compatible, value


SECTOR_SHORT = {
    "XLK.US": "科技", "XLF.US": "金融", "XLV.US": "医疗", "XLY.US": "可选",
    "XLP.US": "必需", "XLE.US": "能源", "XLI.US": "工业", "XLB.US": "材料",
    "XLU.US": "公用", "XLRE.US": "地产", "XLC.US": "通信",
}
DEFENSIVE_SECTORS = frozenset({"XLP.US", "XLV.US", "XLU.US"})
GROWTH_SECTORS = frozenset({"XLK.US", "XLY.US", "XLC.US"})
# Moves at least this large are worth a clause of their own.
CROSS_ASSET_THRESHOLDS = (
    ("BTC-USD.CC", "比特币", 5.0),
    ("GLD.US", "黄金", 2.0),
    ("USO.US", "原油", 3.0),
    ("TLT.US", "长期美债", 1.5),
    ("NYICDX.INDX", "美元指数", 0.7),
)
WATCHLIST_MOVE_PCT = 3.0


def pct(move: Any, digits: int = 2) -> str:
    return value(move, "%", True, digits)


def magnitude(move: float) -> str:
    size = abs(move)
    if size < 0.25:
        return "基本持平"
    if size < 1:
        return "小幅上涨" if move > 0 else "小幅回落"
    if size < 2:
        return "上涨" if move > 0 else "下跌"
    return "大涨" if move > 0 else "大跌"


def current(row: dict | None) -> float | None:
    """Change of a fresh observation; stale rows never feed a conclusion."""
    if not row or not row.get("fresh"):
        return None
    return number(row.get("change_pct"))


def ranked_sectors(quotes: list[dict]) -> list[dict]:
    rows = [row for row in quotes if row.get("group") == "sector" and current(row) is not None]
    return sorted(rows, key=lambda row: row["change_pct"], reverse=True)


def _index_clause(by_symbol: dict, report_type: str) -> str:
    spy, qqq, iwm = (current(by_symbol.get(ticker)) for ticker in ("SPY.US", "QQQ.US", "IWM.US"))
    subject = "美股开盘" if report_type == "morning_briefing" else "美股"
    if spy is not None:
        clause = f"{subject}{magnitude(spy)}（标普 {pct(spy)}）"
    else:
        available = [move for move in (qqq, iwm) if move is not None]
        if not available:
            return "美股当期报价不足"
        clause = f"{subject}{magnitude(sum(available) / len(available))}（标普报价缺失）"
    if qqq is not None and iwm is not None:
        if qqq - iwm >= 1:
            clause += f"，纳指 {pct(qqq)} 强于罗素 {pct(iwm)}"
        elif iwm - qqq >= 1:
            clause += f"，小盘股跑赢：罗素 {pct(iwm)}、纳指 {pct(qqq)}"
    return clause


def _sector_clause(sectors: list[dict], min_spread: float = 0.8) -> str | None:
    """``sectors`` ranked best to worst by change_pct."""
    if len(sectors) < 6:
        return None
    if sectors[0]["change_pct"] - sectors[-1]["change_pct"] < min_spread:
        return "板块分化不大"
    top, bottom = sectors[:2], sectors[-2:][::-1]
    lead = "领涨" if top[0]["change_pct"] > 0 else "相对抗跌"
    lag = "领跌" if bottom[0]["change_pct"] < 0 else "涨幅居后"
    clause = f"{'、'.join(row['name'] for row in top)}{lead}，{'、'.join(row['name'] for row in bottom)}{lag}"
    leaders = {row["ticker"] for row in sectors[:3]}
    if len(leaders & DEFENSIVE_SECTORS) >= 2:
        clause += "（防御板块居前）"
    elif len(leaders & GROWTH_SECTORS) >= 2:
        clause += "（成长板块居前）"
    return clause


def _structure_clause(by_symbol: dict) -> str | None:
    rsp, spy = by_symbol.get("RSP.US"), by_symbol.get("SPY.US")
    if not _compatible(rsp, spy) or current(rsp) is None or current(spy) is None:
        return None
    relative = ((1 + rsp["change_pct"] / 100) / (1 + spy["change_pct"] / 100) - 1) * 100
    if relative >= 0.4:
        tail = "大市值股拖累更明显" if spy["change_pct"] < 0 else "上涨覆盖面较广"
        return f"等权指数跑赢 {pct(relative)}，{tail}"
    if relative <= -0.4:
        tail = "上涨集中在大市值股" if spy["change_pct"] > 0 else "多数个股弱于指数"
        return f"等权指数落后 {pct(relative)}，{tail}"
    return None


def _vix_clause(by_symbol: dict) -> str | None:
    row = by_symbol.get("VIX.INDX")
    move, level = current(row), number((row or {}).get("price"))
    if move is None or level is None:
        return None
    if move >= 10:
        return f"VIX 跳升 {pct(move)} 至 {level:.1f}"
    if move >= 5:
        return f"VIX 升至 {level:.1f}"
    if move <= -10:
        return f"VIX 回落至 {level:.1f}"
    if level >= 25:
        return f"VIX 处于 {level:.1f} 高位"
    return None


def _cross_asset_clause(by_symbol: dict, treasury: dict | None, report_date: str | None) -> str | None:
    candidates = []
    for ticker, name, threshold in CROSS_ASSET_THRESHOLDS:
        move = current(by_symbol.get(ticker))
        if move is not None and abs(move) >= threshold:
            candidates.append((abs(move) / threshold, f"{name} {pct(move)}"))
    if treasury and treasury.get("as_of") == report_date:
        ten = next((row for row in treasury.get("values", []) if row.get("tenor") == "10y"), {})
        change = number(ten.get("change_bp"))
        if change is not None and abs(change) >= 8:
            candidates.append((abs(change) / 8, f"10Y 美债收益率 {value(change, 'bp', True, 0)}"))
    if not candidates:
        return None
    return "、".join(text for _, text in sorted(candidates, reverse=True)[:2])


def _watchlist_clause(quotes: list[dict]) -> str | None:
    moves = [row for row in quotes if row.get("group") == "core" and current(row) is not None
             and abs(row["change_pct"]) >= WATCHLIST_MOVE_PCT]
    if not moves:
        return None
    moves.sort(key=lambda row: abs(row["change_pct"]), reverse=True)
    return "关注列表中 " + "、".join(f"{display_symbol(row)} {pct(row['change_pct'])}" for row in moves[:2])


def display_symbol(row: dict) -> str:
    ticker = str(row.get("ticker") or "")
    base, _, exchange = ticker.rpartition(".")
    return base if exchange in {"US", "INDX", "CC"} else ticker


def weekly_magnitude(move: float) -> str:
    size = abs(move)
    if size < 0.5:
        return "基本持平"
    if size < 2:
        return "小幅上涨" if move > 0 else "小幅回落"
    if size < 4:
        return "上涨" if move > 0 else "下跌"
    return "大涨" if move > 0 else "大跌"


def weekly_headline(digest: dict) -> str:
    by_symbol = {row["ticker"]: row for row in digest.get("assets", []) if number(row.get("change_pct")) is not None}
    spy, qqq, iwm = (number(by_symbol.get(ticker, {}).get("change_pct")) for ticker in ("SPY.US", "QQQ.US", "IWM.US"))
    if spy is None:
        clauses = ["本周标普周线数据不足"]
    else:
        clause = f"本周美股{weekly_magnitude(spy)}（标普 {pct(spy)}）"
        if qqq is not None and iwm is not None and abs(qqq - iwm) >= 2:
            clause += f"，纳指 {pct(qqq)} 强于罗素 {pct(iwm)}" if qqq > iwm else f"，小盘股跑赢：罗素 {pct(iwm)}、纳指 {pct(qqq)}"
        clauses = [clause]
    sectors = sorted((row for row in digest.get("sectors", []) if number(row.get("change_pct")) is not None),
                     key=lambda row: row["change_pct"], reverse=True)
    if sector := _sector_clause(sectors, min_spread=2.0):
        clauses.append(sector)
    extras = []
    breadth = digest.get("breadth") or {}
    now, before = number(breadth.get("pct_above_ma50")), number(breadth.get("previous_pct_above_ma50"))
    if now is not None and before is not None and abs(now - before) >= 5:
        extras.append(f"站上 MA50 的成分股{'升' if now > before else '降'}至 {now:.0f}%（上周 {before:.0f}%）")
    rrg = digest.get("rrg") or {}
    entered = [SECTOR_SHORT.get(item["ticker"], item["ticker"]) for item in rrg.get("items", [])
               if item.get("quadrant") == "leading" and item.get("previous_quadrant") not in (None, "leading")]
    if entered:
        extras.append(f"{'、'.join(entered[:3])}进入轮动领先象限")
    valuation = digest.get("valuation") or {}
    percentile, pe = number(valuation.get("percentile")), number(valuation.get("index_pe"))
    if percentile is not None and pe is not None and (percentile >= 80 or percentile <= 20):
        extras.append(f"标普 P/E {pe:.1f} 位于历史 {percentile:.0f}% 分位")
    movers = [row for row in digest.get("watchlist", []) if number(row.get("change_pct")) is not None
              and abs(row["change_pct"]) >= 5]
    if movers:
        movers.sort(key=lambda row: abs(row["change_pct"]), reverse=True)
        extras.append("关注列表中 " + "、".join(f"{display_symbol(row)} {pct(row['change_pct'])}" for row in movers[:2]))
    clauses.extend(extras[:2])
    return "；".join(clauses) + "。"


def pre_open(by_symbol: dict) -> bool:
    return (by_symbol.get("SPY.US") or {}).get("session", {}).get("state") == "未开盘"


def daily_headline(context: dict, report_type: str) -> str:
    quotes = context.get("quotes", [])
    by_symbol = {row.get("ticker"): row for row in quotes}
    if pre_open(by_symbol):
        # US rows are the previous session; never let them read as today's.
        sector = _sector_clause(ranked_sectors(quotes))
        return "美股尚未开盘" + (f"；前一交易日{sector}" if sector else "") + "。"
    clauses = [_index_clause(by_symbol, report_type)]
    if sector := _sector_clause(ranked_sectors(quotes)):
        clauses.append(sector)
    extras = [
        _structure_clause(by_symbol),
        _vix_clause(by_symbol),
        _cross_asset_clause(by_symbol, context.get("treasury"), context.get("report_date")),
        _watchlist_clause(quotes),
    ]
    clauses.extend([clause for clause in extras if clause][:2])
    return "；".join(clauses) + "。"
