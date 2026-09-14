"""Collect real evidence and save a report preview without sending notifications.

Usage: python scripts/preview_daily_report.py --output /path/to/preview.md
The companion JSON contains the exact evidence needed to reproduce the text.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from database import async_session_maker
from models import AnomalyScanRun
from services.daily_reporter import render_daily_report
from services.report_market import collect_market_context


async def preview(output: Path, report_type: str) -> dict:
    context = await collect_market_context()
    async with async_session_maker() as db:
        saved = await db.scalar(select(AnomalyScanRun).where(AnomalyScanRun.status == "completed")
                                .order_by(AnomalyScanRun.finished_at.desc()).limit(1))
        anomalies = [row for row in (saved.results if saved else []) or []
                     if row.get("date") == context["report_date"]]
    limit = 5 if report_type == "morning_briefing" else 10
    anomalies = anomalies[:limit]
    context["warnings"].append("本地预览：行情按实际采集时点展示；个股异动仅复用同交易日已保存扫描，未另行扫描或发送。")
    content = render_daily_report(anomalies, report_type=report_type, market_context=context)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "> 本地格式预览：按实际采集时点展示，可能处于盘中，并非正式定时报送。\n\n" + content + "\n",
        encoding="utf-8",
    )
    output.with_suffix(".json").write_text(json.dumps(
        {"report_type": report_type, "anomalies": anomalies, "market_context": context},
        ensure_ascii=False, indent=2, allow_nan=False,
    ) + "\n", encoding="utf-8")
    return {"preview": str(output), "evidence": str(output.with_suffix('.json')),
            "quote_count": len(context["quotes"]),
            "current_quotes": sum(bool(row["fresh"]) for row in context["quotes"]),
            "missing_quotes": [row["ticker"] for row in context["quotes"] if row["price"] is None],
            "report_bytes": len(content.encode("utf-8")), "warnings": context["warnings"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-type", choices=("morning_briefing", "post_market_summary"), default="post_market_summary")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(preview(args.output, args.report_type)), ensure_ascii=False, indent=2))
