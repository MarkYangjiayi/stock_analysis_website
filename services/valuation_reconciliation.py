"""Apply auditable financial facts only to the exact reviewed source statement.

These are financial-data corrections, not ticker-specific model parameters.
New filings automatically invalidate old corrections until they are reviewed.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

from services.valuation_inputs import number

PACKET_KEY = "_valuation_reconciliation"
ALLOWED_BALANCE_FIELDS = {"totalDebt", "debtScope", "cashAndShortTermInvestments", "commonStockSharesOutstanding", "sharesBasis", "period_end", "valuationExcessCash", "valuationNonOperatingAssets", "valuationNoncontrollingInterests", "valuationPreferredEquity"}
ALLOWED_INCOME_FIELDS = {"interestExpenseNonOperating", "interestIncomeNonOperating", "period_end"}
ALLOWED_CASH_FIELDS = {"freeCashFlow", "totalCashFromOperatingActivities", "capitalExpenditures"}


def statement_fingerprint(income: dict, balance: dict, cash_flow: dict) -> str:
    cleaned = {k: v for k, v in balance.items() if k != PACKET_KEY}
    return hashlib.sha256(json.dumps([income, cleaned, cash_flow], sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def apply_reconciliation(income: dict, balance: dict, cash_flow: dict, *, as_of: date | None = None) -> tuple[dict, dict, dict, list[str]]:
    packet = balance.get(PACKET_KEY)
    if not packet:
        return income, balance, cash_flow, []
    try:
        if packet["input_sha256"] != statement_fingerprint(income, balance, cash_flow):
            raise ValueError("Source statement has changed since reconciliation.")
        if date.fromisoformat(packet["available_at"]) > (as_of or date.today()):
            raise ValueError("Reconciliation evidence is not yet available.")
        period_end = date.fromisoformat(packet["period_end"])
        if date.fromisoformat(packet["available_at"]) < period_end:
            raise ValueError("Financial evidence availability precedes its reported period end.")
        label = date.fromisoformat(str(balance.get("date") or income.get("date")))
        if abs((period_end - label).days) > 7:
            raise ValueError("Reconciliation fiscal period does not match the statement.")
        merged = [dict(income), dict(balance), dict(cash_flow)]
        for index, name, allowed in ((0, "income", ALLOWED_INCOME_FIELDS), (1, "balance", ALLOWED_BALANCE_FIELDS), (2, "cash_flow", ALLOWED_CASH_FIELDS)):
            entries = packet.get(name, {})
            if set(entries) - allowed:
                raise ValueError("Unrecognized reconciliation field.")
            for key, fact in entries.items():
                if not fact.get("source_url", "").startswith("https://") or not str(fact.get("definition") or "").strip():
                    raise ValueError("Each financial correction needs a source URL and definition.")
                value = fact["value"]
                if key not in {"debtScope", "sharesBasis", "period_end"} and (number(value) is None or (index == 1 and number(value) < 0)):
                    raise ValueError("Invalid financial correction value.")
                merged[index][key] = value
            if index == 1 and "totalDebt" in entries:
                # Raw aggregates remain in the packet's fingerprinted source,
                # but must not conflict with the reconciled debt definition.
                merged[index].pop("shortLongTermDebtTotal", None)
                for component in ("debtCurrent", "shortTermDebt", "shortTermDebtTotal", "shortLongTermDebt", "longTermDebtNoncurrent", "longTermDebt", "longTermDebtTotal"):
                    merged[index].pop(component, None)
        merged[1]["period_end"] = packet["period_end"]
        return *merged, ["Source-linked financial reconciliation applied to the matching statement."]
    except (KeyError, TypeError, ValueError) as exc:
        return income, balance, cash_flow, [f"Financial reconciliation not applied: {exc}"]
