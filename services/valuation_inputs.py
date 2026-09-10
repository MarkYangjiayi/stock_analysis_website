"""Shared, lossless financial-input contract for valuation and financial views.

Missing components are never zeros. EODHD's capitalLeaseObligations is not
added to debt: in real filings it can overlap totals or describe operating
leases. Reported aggregates retain their lease-scope qualification.
"""
from __future__ import annotations

import math
from typing import Any


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def field(mapping: dict, *keys: str) -> tuple[float | None, str | None]:
    for key in keys:
        value = number(mapping.get(key))
        if value is not None:
            return value, key
    return None, None


def resolve_debt(balance: dict) -> dict[str, Any]:
    current, current_key = field(balance, "debtCurrent", "shortTermDebtTotal", "shortTermDebt", "shortLongTermDebt")
    long, long_key = field(balance, "longTermDebtNoncurrent", "longTermDebtTotal", "longTermDebt")
    components = {k: v for k, v in ((current_key, current), (long_key, long)) if k is not None}
    candidates = {k: number(balance.get(k)) for k in ("totalDebt", "shortLongTermDebtTotal")}
    candidates = {k: v for k, v in candidates.items() if v is not None}
    notes: list[str] = []
    invalid = [k for k, v in {**components, **candidates}.items() if v < 0]
    if invalid:
        return {"value": None, "source": None, "scope": "unresolved", "components": components, "reported_totals": candidates, "notes": [f"Negative debt fields require reconciliation: {', '.join(invalid)}."], "available": False}
    lower_bound = max(components.values(), default=0)
    usable = {k: v for k, v in candidates.items() if v + max(1.0, abs(v) * 0.005) >= lower_bound}
    if len(usable) == 2 and not math.isclose(*usable.values(), rel_tol=0.005, abs_tol=1.0):
        return {"value": None, "source": None, "scope": "unresolved", "components": components, "reported_totals": candidates, "notes": ["Conflicting reported debt totals require reconciliation."], "available": False}
    if usable:
        source, value = next(iter(usable.items()))
        if current is None or long is None:
            notes.append("Complete reported debt total used; unavailable short/long components were not treated as zero.")
        elif not math.isclose(value, current + long, rel_tol=0.005, abs_tol=1):
            notes.append("Reported debt total differs from short/long components; lease and other financing scope is not fully classified.")
        scope = str(balance.get("debtScope") or "provider_total_lease_scope_unverified")
        if scope == "provider_total_lease_scope_unverified":
            notes.append("Reported debt may include leases; no ambiguous lease field was added a second time.")
    elif current is not None and long is not None:
        value, source, scope = current + long, f"{current_key} + {long_key}", "current_plus_noncurrent_debt"
        if candidates:
            notes.append("Reported total was smaller than a debt component; complete current/noncurrent components were used.")
    else:
        value, source, scope = None, None, "unresolved"
        notes.append("Complete debt total or both current and noncurrent debt are required; partial debt is not a total.")
    operating_leases, lease_key = field(balance, "operatingLeaseLiabilities", "operatingLeaseLiability")
    return {"value": value, "source": source, "scope": scope, "components": components, "reported_totals": candidates,
            "operating_leases": operating_leases, "operating_lease_source": lease_key,
            "notes": notes, "available": value is not None}


def statement_inputs(income: dict, balance: dict, cash_flow: dict) -> dict[str, Any]:
    from services.valuation_reconciliation import apply_reconciliation
    income, balance, cash_flow, reconciliation_notes = apply_reconciliation(income, balance, cash_flow)
    debt = resolve_debt(balance)
    cfo, cfo_source = field(cash_flow, "totalCashFromOperatingActivities", "operatingCashFlow")
    capex, capex_source = field(cash_flow, "capitalExpenditures", "capitalExpenditure")
    reported_fcf, reported_source = field(cash_flow, "freeCashFlow")
    fcf = cfo - abs(capex) if cfo is not None and capex is not None else reported_fcf
    fcf_source = f"{cfo_source} - abs({capex_source})" if cfo is not None and capex is not None else reported_source
    cash, cash_source = field(balance, "cashAndShortTermInvestments", "cashAndCashEquivalents", "cashAndEquivalents", "cash")
    interest, interest_source = field(income, "interestExpenseNonOperating", "interestExpense")
    interest_income, interest_income_source = field(income, "interestIncomeNonOperating", "investmentIncomeInterestNonOperating")
    shares, shares_source = field(balance, "commonStockSharesOutstanding", "sharesOutstanding")
    notes = reconciliation_notes + list(debt["notes"])
    if cfo is None or capex is None:
        notes.append("CFO/capex bridge is incomplete; provider FCF definition is unverified.")
    if reported_fcf is not None and fcf is not None and not math.isclose(fcf, reported_fcf, rel_tol=0.005, abs_tol=1):
        notes.append("Provider FCF differs from CFO minus capex; the explicit CFO/capex definition is used.")
    if cash_source == "cashAndShortTermInvestments":
        notes.append("Cash bridge includes short-term investments; availability for distribution is not verified.")
    if interest_income is None and cash is not None and cash > 0:
        notes.append("Investment interest income is unavailable; the FCFF bridge may retain non-operating income.")
    if shares is not None and not balance.get("sharesBasis"):
        notes.append("Statement share count is provider-reported; point-in-time versus weighted-average basis is unverified.")
    minority, minority_source = field(balance, "noncontrollingInterestInConsolidatedEntity", "minorityInterest")
    if minority is not None and minority != 0:
        notes.append("Noncontrolling interests are present; book value is not automatically a market-value equity deduction.")
    excess_cash = number(balance.get("valuationExcessCash"))
    other_assets = number(balance.get("valuationNonOperatingAssets"))
    minority_value = number(balance.get("valuationNoncontrollingInterests"))
    preferred = number(balance.get("valuationPreferredEquity"))
    bridge = {
        "reported_cash": cash,
        "cash_added": excess_cash if excess_cash is not None else cash,
        "cash_basis": "sourced_excess_cash" if excess_cash is not None else "reported_cash_proxy_unverified",
        "nonoperating_assets_added": other_assets,
        "noncontrolling_interests_deducted": minority_value,
        "preferred_equity_deducted": preferred,
        "equity_adjustment": (other_assets or 0) - (minority_value or 0) - (preferred or 0),
        "complete": all(value is not None for value in (excess_cash, other_assets, minority_value, preferred)),
    }
    if not bridge["complete"]:
        notes.append("Equity bridge is a qualified estimate: unverified excess cash, non-operating assets and non-common claims are not silently treated as verified zeros.")
    return {"debt": debt, "fcf": fcf, "fcf_source": fcf_source, "provider_fcf": reported_fcf,
            "cfo": cfo, "capex": abs(capex) if capex is not None else None,
            "cash": cash, "cash_source": cash_source, "equity_bridge": bridge, "shares": shares, "shares_source": shares_source,
            "shares_basis": balance.get("sharesBasis", "provider_statement_basis_unverified"),
            "reported_period_end": balance.get("period_end") or income.get("period_end"),
            "reconciliation": balance.get("_valuation_reconciliation"),
            "interest_expense": interest, "interest_expense_source": interest_source,
            "interest_income": interest_income, "interest_income_source": interest_income_source,
            "unclassified_interest_income": number(income.get("interestIncome")),
            "noncontrolling_interest_book": minority, "noncontrolling_interest_source": minority_source,
            "unavailable_reasons": [note for note in reconciliation_notes if "not applied" in note],
            "notes": notes}
