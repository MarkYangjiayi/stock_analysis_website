"""Quarantine conflicting quarterly inputs without repairing or clipping values."""
from __future__ import annotations

import math

from services.valuation_inputs import field, number, resolve_debt


def statement_quality(income: dict, balance: dict, cash: dict) -> dict[str, str]:
    """Return affected input keys: eps, fcf, ebitda and debt.

    Cross-statement amounts are compared only with explicit matching period and
    currency labels. A difference establishes unresolved scope, not which source
    is correct. The caller checks TTM flows across all four quarters and debt
    only on the latest balance. No small-denominator threshold is applied.
    """
    reasons = {}
    net = number(income.get("netIncome"))
    common = number(income.get("netIncomeApplicableToCommonShares"))
    if common == 0 and net is not None and net < 0:
        reasons["eps"] = "Common-share earnings are zero while reported net income is negative; earnings require reconciliation."

    income_date = str(income.get("date") or income.get("period_end") or "")[:10]
    cash_date = str(cash.get("date") or cash.get("period_end") or "")[:10]
    income_currency = str(income.get("currency_symbol") or "").strip().upper()
    cash_currency = str(cash.get("currency_symbol") or "").strip().upper()
    cash_net = number(cash.get("netIncome"))
    # CFO can start with consolidated earnings while the income statement's
    # netIncome is attributable to the parent. Its signed minority-income line
    # can reconcile that difference; balance-sheet minority equity cannot.
    minority = number(income.get("minorityInterest"))
    minority_bridge = (cash_net is not None and net is not None and minority is not None
                       and math.isclose(cash_net + minority, net, rel_tol=0.005, abs_tol=1.0))
    pretax, tax = number(income.get("incomeBeforeTax")), number(income.get("incomeTaxExpense"))
    # A missing attribution line need not invalidate profitable consolidated
    # earnings that exactly reconcile to pretax income less tax. Never bypass
    # an explicit conflicting minority/common earnings amount with this bridge.
    tax_bridge = (minority is None and net is not None and cash_net is not None and 0 <= net < cash_net
                  and pretax is not None and tax is not None
                  and math.isclose(pretax - tax, cash_net, rel_tol=1e-9, abs_tol=1)
                  and (common is None or math.isclose(common, net, rel_tol=1e-9, abs_tol=1)))
    if (income_date and income_date == cash_date and income_currency
            and income_currency == cash_currency and net is not None and cash_net is not None
            and not (minority_bridge or tax_bridge)
            and not math.isclose(net, cash_net, rel_tol=0.005, abs_tol=1.0)):
        reason = "Income and cash-flow statements report different net income for the same period; earnings and cash-flow scope require reconciliation."
        reasons.setdefault("eps", reason)
        reasons["fcf"] = reason

    balance_date = str(balance.get("date") or balance.get("period_end") or "")[:10]
    balance_currency = str(balance.get("currency_symbol") or "").strip().upper()
    balance_cash, _ = field(balance, "cashAndCashEquivalents", "cashAndEquivalents", "cash")
    ending_cash = number(cash.get("endPeriodCashFlow"))
    # Cash-flow cash may additionally include restricted cash. Only a material
    # shortfall against the narrower balance-sheet cash balance is a conflict.
    if (balance_date and balance_date == cash_date and balance_currency
            and balance_currency == cash_currency and balance_cash is not None and ending_cash is not None
            and balance_cash - ending_cash > max(1.0, .02 * max(abs(balance_cash), abs(ending_cash)))):
        reasons["fcf"] = "Cash-flow ending cash is below same-period balance-sheet cash and equivalents; cash-flow inputs require reconciliation."

    revenue = number(income.get("totalRevenue"))
    operating = number(income.get("operatingIncome"))
    ebitda = number(income.get("ebitda"))
    if (number(income.get("costOfRevenue")) is None and revenue is not None and revenue > 0
            and operating is not None and ebitda is not None
            and math.isclose(operating, revenue, rel_tol=1e-9, abs_tol=1)
            and math.isclose(ebitda, revenue, rel_tol=1e-9, abs_tol=1)):
        reasons["ebitda"] = "EBITDA and operating income equal revenue while cost of revenue is missing; EBITDA may contain a placeholder."

    debt = resolve_debt(balance)
    current, _ = field(balance, "debtCurrent", "shortTermDebtTotal", "shortTermDebt", "shortLongTermDebt")
    long, _ = field(balance, "longTermDebtNoncurrent", "longTermDebtTotal", "longTermDebt")
    scope = str(balance.get("debtScope") or "").strip().lower()
    explicit_scope = scope and scope not in {
        "provider_total_lease_scope_unverified", "unresolved", "unverified", "unknown", "short_term_only",
    }
    if (debt["source"] in debt["reported_totals"] and debt["value"] is not None
            and current is not None and current > 0 and long is None and not explicit_scope
            and math.isclose(debt["value"], current, rel_tol=1e-9, abs_tol=1)):
        reason = "Reported total debt equals short-term debt but long-term debt is missing; complete debt scope is unverified."
        reasons["debt"] = reason
    return reasons
