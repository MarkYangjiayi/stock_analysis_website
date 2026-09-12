import copy

import pytest

from services.valuation_history_quality import statement_quality


def test_zero_common_earnings_do_not_erase_a_reported_loss():
    income = {"netIncomeApplicableToCommonShares": "0.00", "netIncome": "-223608000.00"}
    assert set(statement_quality(income, {}, {})) == {"eps"}
    assert "zero" in statement_quality(income, {}, {})["eps"]
    assert income["netIncomeApplicableToCommonShares"] == "0.00"


@pytest.mark.parametrize("earnings", [0.000001, 0, -0.000001, -234131000])
def test_consistent_earnings_are_not_rejected_for_their_magnitude_or_sign(earnings):
    income = {"date": "2001-03-31", "currency_symbol": "USD", "netIncome": earnings,
              "netIncomeApplicableToCommonShares": earnings}
    cash = {"date": "2001-03-31", "currency_symbol": "USD", "netIncome": earnings}
    assert statement_quality(income, {}, cash) == {}


def test_same_period_earnings_scope_conflict_quarantines_pe_and_pfcf():
    income = {"date": "2001-03-31", "currency_symbol": "USD", "netIncome": -223608000}
    cash = {"date": "2001-03-31", "currency_symbol": "usd", "netIncome": -234131000}
    assert set(statement_quality(income, {}, cash)) == {"eps", "fcf"}


@pytest.mark.parametrize("net,cash_net,minority", [(5254000000, 5425000000, -171000000),
                                                  (-821000000, -887000000, 66000000),
                                                  (1203000000, 873000000, 330000000)])
def test_signed_minority_income_reconciles_consolidated_cash_flow_earnings(net, cash_net, minority):
    income = {"date": "2024-12-31", "currency_symbol": "USD", "netIncome": net,
              "minorityInterest": minority}
    cash = {"date": "2024-12-31", "currency_symbol": "USD", "netIncome": cash_net}
    assert statement_quality(income, {}, cash) == {}
    assert set(statement_quality({**income, "minorityInterest": 0}, {}, cash)) == {"eps", "fcf"}
    # Stock balances do not reconcile a quarterly earnings flow.
    assert set(statement_quality({**income, "minorityInterest": None}, {"minorityInterest": minority}, cash)) == {"eps", "fcf"}


def test_missing_minority_line_can_reconcile_through_consolidated_pretax_less_tax():
    income = {"date": "2026-07-31", "currency_symbol": "USD", "netIncome": 6366000000,
              "incomeBeforeTax": 8012000000, "incomeTaxExpense": 1483000000}
    cash = {"date": "2026-07-31", "currency_symbol": "USD", "netIncome": 6529000000}
    assert statement_quality(income, {}, cash) == {}
    assert statement_quality({**income, "netIncomeApplicableToCommonShares": 6366000000}, {}, cash) == {}


@pytest.mark.parametrize("change", [{"minorityInterest": 0},
                                   {"netIncomeApplicableToCommonShares": 6000000000},
                                   {"netIncome": -1000000}, {"netIncome": 6700000000},
                                   {"incomeBeforeTax": 8013000000}, {"incomeTaxExpense": None}])
def test_tax_bridge_does_not_bypass_explicit_conflicts_or_infer_loss_attribution(change):
    income = {"date": "2026-07-31", "currency_symbol": "USD", "netIncome": 6366000000,
              "incomeBeforeTax": 8012000000, "incomeTaxExpense": 1483000000, **change}
    cash = {"date": "2026-07-31", "currency_symbol": "USD", "netIncome": 6529000000}
    assert set(statement_quality(income, {}, cash)) == {"eps", "fcf"}


@pytest.mark.parametrize("change", [{"date": "2001-06-30"}, {"date": None},
                                   {"currency_symbol": "EUR"}, {"currency_symbol": None},
                                   {"netIncome": -223608001}])
def test_unmatched_scope_or_rounding_does_not_create_a_false_conflict(change):
    income = {"date": "2001-03-31", "currency_symbol": "USD", "netIncome": -223608000}
    cash = {"date": "2001-03-31", "currency_symbol": "USD", "netIncome": -234131000, **change}
    assert statement_quality(income, {}, cash) == {}


def test_revenue_placeholder_isolated_from_real_zero_cost_and_other_metrics():
    income = {"totalRevenue": "176400000", "operatingIncome": "176400000",
              "ebitda": "176400000", "costOfRevenue": None}
    assert set(statement_quality(income, {}, {})) == {"ebitda"}
    assert statement_quality({**income, "costOfRevenue": 0}, {}, {}) == {}
    assert statement_quality({**income, "operatingIncome": 50000000}, {}, {}) == {}


@pytest.mark.parametrize("aggregate", ["shortLongTermDebtTotal", "totalDebt"])
def test_reported_debt_must_not_silently_stand_for_short_term_debt(aggregate):
    balance = {aggregate: 1575000000, "shortTermDebt": 1575000000, "longTermDebt": None}
    assert set(statement_quality({}, balance, {})) == {"debt"}
    assert statement_quality({}, {**balance, "longTermDebt": 0}, {}) == {}
    assert statement_quality({}, {**balance, "debtScope": "reported_current_and_noncurrent_borrowings"}, {}) == {}
    assert set(statement_quality({}, {**balance, "debtScope": "provider_total_lease_scope_unverified"}, {})) == {"debt"}
    assert statement_quality({}, {**balance, aggregate: 12194000000}, {}) == {}


def test_zero_debt_and_missing_components_do_not_imply_hidden_positive_debt():
    assert statement_quality({}, {"totalDebt": 0, "shortTermDebt": 0}, {}) == {}
    assert statement_quality({}, {"totalDebt": 100}, {}) == {}


def test_quality_checks_do_not_change_source_statements():
    statements = ({"netIncome": -10, "netIncomeApplicableToCommonShares": 0},
                  {"totalDebt": 100, "shortTermDebt": 100}, {"netIncome": -20})
    before = copy.deepcopy(statements)
    statement_quality(*statements)
    assert statements == before


def test_cash_flow_ending_cash_must_cover_same_period_cash_balance():
    balance = {"date": "2026-06-30", "currency_symbol": "USD", "cash": 20422000000}
    cash = {"date": "2026-06-30", "currency_symbol": "USD", "endPeriodCashFlow": 9907000000}
    assert set(statement_quality({}, balance, cash)) == {"fcf"}
    assert "ending cash" in statement_quality({}, balance, cash)["fcf"]


@pytest.mark.parametrize("balance_change,cash_change", [
    ({"date": "2026-03-31"}, {}),
    ({"currency_symbol": "EUR"}, {}),
    ({"date": None}, {}),
    ({"currency_symbol": None}, {}),
    ({"cash": None, "cashAndShortTermInvestments": 30000000000}, {}),
    ({}, {"endPeriodCashFlow": 21000000000}),  # Additional restricted cash is valid.
    ({}, {"endPeriodCashFlow": 20422000000}),
    ({}, {"endPeriodCashFlow": 20421000000}),  # Immaterial rounding.
])
def test_cash_scope_or_missing_metadata_do_not_create_a_false_conflict(balance_change, cash_change):
    balance = {"date": "2026-06-30", "currency_symbol": "USD", "cash": 20422000000, **balance_change}
    cash = {"date": "2026-06-30", "currency_symbol": "USD", "endPeriodCashFlow": 9907000000, **cash_change}
    assert statement_quality({}, balance, cash) == {}
