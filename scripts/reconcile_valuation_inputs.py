#!/usr/bin/env python3
"""Preview/apply source-linked corrections without changing original statements.

By default this only previews. --apply persists matching packets to the
configured database. A changed source payload fails closed and requires review.
"""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import sys
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from database import async_session_maker
from models import FinancialStatement
from services.valuation_reconciliation import PACKET_KEY, apply_reconciliation, statement_fingerprint


async def run(path: Path, apply: bool):
    packets = json.loads(path.read_text())["records"]
    report = []
    async with async_session_maker() as db:
        pending = []
        for entry in packets:
            result = await db.execute(select(FinancialStatement).where(
                FinancialStatement.ticker == entry["ticker"], FinancialStatement.period == entry["period"],
                FinancialStatement.fiscal_date == date.fromisoformat(entry["provider_period_end"])))
            row = result.scalar_one_or_none()
            if row is None:
                report.append({"ticker": entry["ticker"], "status": "statement_not_found"})
                continue
            packet = entry["reconciliation"]
            inc, bal, cf = row.income_statement or {}, row.balance_sheet or {}, row.cash_flow or {}
            updated = {**bal, PACKET_KEY: packet}
            _, _, _, notes = apply_reconciliation(inc, updated, cf)
            if packet["input_sha256"] != statement_fingerprint(inc, bal, cf) or any("not applied" in n for n in notes):
                report.append({"ticker": entry["ticker"], "status": "source_mismatch", "notes": notes})
                continue
            report.append({"ticker": entry["ticker"], "status": "matched", "fields": list(packet.get("balance", {}))})
            pending.append((row, updated))
        if apply:
            if any(x["status"] != "matched" for x in report):
                raise ValueError("No corrections were applied: every packet must match its source statement.")
            for row, updated in pending:
                row.balance_sheet = updated
            await db.commit()
    print(json.dumps({"applied": apply, "records": report}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.input, args.apply))
