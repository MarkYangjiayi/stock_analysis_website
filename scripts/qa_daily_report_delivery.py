"""Manually exercise reporting nodes; opt-in delivery, no scheduler/DB writes.

Prepare with --case morning|post|degraded --output-dir PATH. Then inspect the
saved Markdown and send that exact artifact with --deliver (no recollection).
This deliberately does not claim to exercise a production scan or scheduler.
"""
import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import eodhd_client, report_market
from services.daily_reporter import REPORT_RENDERER_VERSION, render_daily_report
from services.notifications import NotificationManager
from services.notifications.report_card import build_daily_report_card
from core.config import settings


def save(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


async def collect_audited():
    raw = {"live": {}, "daily": {}, "rates": None}
    live, daily, rates = eodhd_client.get_live_quotes, eodhd_client.get_eod_historical_data, report_market.get_yield_curve

    async def capture_live(*args, **kwargs):
        rows = await live(*args, **kwargs)
        raw["live"].update({row["code"]: deepcopy(row) for row in rows})
        return rows

    async def capture_daily(ticker, *args, **kwargs):
        rows = await daily(ticker, *args, **kwargs)
        raw["daily"][ticker] = deepcopy(rows)
        return rows

    async def capture_rates(*args, **kwargs):
        payload = await rates(*args, **kwargs)
        raw["rates"] = deepcopy(payload)
        return payload

    with patch.object(eodhd_client, "get_live_quotes", capture_live), patch.object(
        eodhd_client, "get_eod_historical_data", capture_daily
    ), patch.object(report_market, "get_yield_curve", capture_rates):
        context = await report_market.collect_market_context()
    checks = []
    for row in context["quotes"]:
        if row["price"] is None:
            continue
        if row["basis"] == "delayed_snapshot":
            source = raw["live"][row["ticker"]]
            before, after = source.get("previousClose"), source["close"]
        else:
            sources = {r["date"]: r for r in raw["daily"][row["ticker"]]}
            source = sources[row["session_date"]]
            key = "adjusted_close" if row["basis"] == "eod_adjusted" else "close"
            before = sources.get(row.get("previous_date"), {}).get(key)
            after = source.get(key)
        assert row["price"] == float(source["close"]), row["ticker"]
        expected = (float(after) / float(before) - 1) * 100 if before and after else None
        if expected is not None:
            assert abs(row["change_pct"] - expected) <= 0.000051, row["ticker"]
        checks.append({"ticker": row["ticker"], "price_matches_raw": True,
                       "expected_change_pct": expected, "reported_change_pct": row["change_pct"],
                       "as_of": row["as_of"]})
    if context["treasury"]:
        observations = {r["date"]: r["yields"] for r in raw["rates"]["observations"]}
        current = observations[context["treasury"]["as_of"]]
        previous = observations.get(context["treasury"].get("previous_date"), {})
        for row in context["treasury"]["values"]:
            tenor = row["tenor"]
            assert row["yield_pct"] == current[tenor]
            if current[tenor] is not None and previous.get(tenor) is not None:
                assert abs(row["change_bp"] - (current[tenor] - previous[tenor]) * 100) < 0.00001
        assert abs(context["treasury"]["spread_10y_2y_bp"] - (current["10y"] - current["2y"]) * 100) < 0.00001
    return context, raw, checks


async def prepare(case, directory):
    report_type = "morning_briefing" if case == "morning" else "post_market_summary"
    anomalies, scan = [], None
    if case == "degraded":
        original = json.loads((directory / "morning.json").read_text())
        context, raw, checks = deepcopy(original["market_context"]), {}, []
        context["warnings"] = [item for item in context.get("warnings", []) if not item.startswith("手动验收，")]
        # Retain real saved prices but deliberately mask inputs; no fake moves.
        for row in context["quotes"]:
            if row["ticker"] == "SPY.US":
                row.update(price=None, change_pct=None, fresh=False, status="unavailable", status_label="验收注入缺失")
            elif row["ticker"] == "QQQ.US":
                row.update(fresh=False, status="stale", status_label="验收注入过期（非实际行情状态）")
        context.update(treasury=None, events=None, breadth=None, morning_snapshot=None)
        notice = "异常分支验收：人为屏蔽 SPY、美债、日历及宽度，人为标记 QQQ 过期；以下不代表实际数据可用状态。QA_INVALID 是虚构测试标识，非真实证券。"
        anomalies = [{"ticker": "QA_INVALID.US", "price_change": None, "date": context["report_date"],
                      "attribution_status": "completed", "ai_analysis": "不应展示的合成归因[99]", "news": []}]
    else:
        context, raw, checks = await collect_audited()
        notice = "手动验收，非正式定时报送；行情按真实采集时点展示，当前可能处于盘中。"
        if case == "post":
            morning = json.loads((directory / "morning.json").read_text())
            context["morning_snapshot"] = morning["market_context"] if morning.get("delivered") else None
            notice += "此卡仅验证盘后模板，不是收盘行情；早报对比基准为本次已送达的验收早报卡。"
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get("https://finbrain.icu/api/market/anomalies")
            response.raise_for_status()
            scan = response.json()
        if scan.get("status") == "completed":
            keys = ("ticker", "company_name", "date", "quote_timestamp", "price_change", "attribution_status", "ai_analysis", "news")
            anomalies = [{k: row.get(k) for k in keys} for row in scan.get("results", []) if row.get("date") == context["report_date"]]
        notice += f"个股异动复用生产已保存扫描 #{scan.get('id')}（最晚报价 {scan.get('quote_as_of')}），不是本次重新扫描，不能作为当前涨跌。"
        scan = {k: scan.get(k) for k in ("id", "status", "trigger", "quote_as_of", "finished_at")}
    context.setdefault("warnings", []).append(notice)
    label = {"morning": "开盘模板·真实采集", "post": "盘后模板·盘中验收", "degraded": "异常分支·人为故障注入"}[case]
    title = f"🧪 Quantify QA {directory.name}｜{label}"
    content = "**测试卡片，请勿作为正式报送或交易依据**\n\n" + render_daily_report(anomalies, report_type=report_type, market_context=context)
    if case == "degraded":
        for expected in ("数据不可用", "验收注入过期", "美债曲线暂不可用", "日历暂不可用", "归因引用无法与已保存新闻匹配"):
            assert expected in content, expected
        assert "不应展示的合成归因" not in content
    payload = {"title": title, "content": content, "report_type": report_type, "market_context": context,
               "anomalies": anomalies, "scan": scan, "raw": raw, "checks": checks, "delivered": False}
    save(directory / f"{case}.json", payload)
    (directory / f"{case}.md").write_text(content + "\n", encoding="utf-8")
    return {"prepared": case, "raw_price_checks": len(checks), "fresh": sum(bool(r["fresh"]) for r in context["quotes"]),
            "quote_count": len(context["quotes"]), "bytes": len(content.encode()), "title": title}


async def main(args):
    directory = args.output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{args.case}.json"
    if not args.deliver:
        if path.exists():
            raise RuntimeError("Artifact already exists; use a new output directory to avoid overwriting evidence")
        if args.replay:
            original = json.loads(args.replay.read_text())
            context = deepcopy(original["market_context"])
            context["warnings"] = [warning for warning in context.get("warnings", []) if not warning.startswith("手动验收，")]
            context["warnings"].append("历史回放验收：仅使用已保存数据，不是当前行情或正式定时报送。")
            title = f"Quantify QA {REPORT_RENDERER_VERSION}｜历史回放·{args.case}"
            content = render_daily_report(original["anomalies"], report_type=original["report_type"], market_context=context)
            card = build_daily_report_card(title, content)
            save(path, {"title": title, "content": content, "card": card, "renderer_version": REPORT_RENDERER_VERSION,
                        "report_type": original["report_type"], "market_context": context,
                        "anomalies": original["anomalies"], "source_artifact": str(args.replay.resolve()), "delivered": False})
            (directory / f"{args.case}.md").write_text(content + "\n", encoding="utf-8")
            save(directory / f"{args.case}.card.json", card)
            visible = json.dumps(card["body"]["elements"][:2], ensure_ascii=False)
            return {"replayed": args.case, "renderer_version": REPORT_RENDERER_VERSION,
                    "report_bytes": len(content.encode()), "first_screen_component_bytes": len(visible.encode()),
                    "panels": len(card["body"]["elements"]) - 2, "title": title}
        return await prepare(args.case, directory)
    payload = json.loads(path.read_text())
    if payload.get("card") and payload["card"] != build_daily_report_card(payload["title"], payload["content"]):
        raise RuntimeError("Card renderer changed since preparation; create a new preview before sending")
    if payload.get("delivery_attempted_at"):
        raise RuntimeError("Delivery already attempted; inspect Feishu before creating any new attempt")
    payload["delivery_attempted_at"] = datetime.now(timezone.utc).isoformat()
    save(path, payload)
    post = httpx.AsyncClient.post

    async def observed_post(client, *args, **kwargs):
        try:
            response = await post(client, *args, **kwargs)
            body = response.json()
            message = str(body.get("msg", body.get("StatusMessage", "")))
            for secret in (settings.FEISHU_WEBHOOK_URL, settings.EODHD_API_KEY):
                if secret:
                    message = message.replace(secret, "[redacted]")
            payload["transport"] = {"http_status": response.status_code, "code": body.get("code"),
                                    "status_code": body.get("StatusCode"), "message": message[:300]}
            return response
        except Exception as exc:
            payload["transport"] = {"error_type": type(exc).__name__}
            raise

    with patch.object(httpx.AsyncClient, "post", observed_post):
        payload["delivered"] = await NotificationManager.broadcast(title=payload["title"], content=payload["content"], channels=["feishu"], card_layout="daily_report")
    save(path, payload)
    if not payload["delivered"]:
        raise RuntimeError(f"Feishu did not acknowledge delivery: {payload.get('transport')}; inspect the chat, do not blindly retry")
    return {"delivered": True, "title": payload["title"]}


if __name__ == "__main__":
    # Avoid transport logs containing credential-bearing provider/webhook URLs.
    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("morning", "post", "degraded"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--deliver", action="store_true")
    parser.add_argument("--replay", type=Path, help="Replay a saved evidence JSON without any data-source calls")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(main(args)), ensure_ascii=False, indent=2))
