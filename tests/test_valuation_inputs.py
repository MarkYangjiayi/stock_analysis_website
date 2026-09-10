import pytest

from services.valuation_inputs import resolve_debt, statement_inputs


@pytest.mark.parametrize("short,total", [(1.509, 38.860), (16.543, 73.755), (3.827, 73.328)])
def test_partial_debt_does_not_hide_reported_total(short, total):
    debt = resolve_debt({"shortTermDebt": short, "shortLongTermDebtTotal": total})
    assert debt["value"] == total
    assert debt["source"] == "shortLongTermDebtTotal"


def test_complete_components_reject_impossible_total_without_adding_ambiguous_leases():
    debt = resolve_debt({"shortLongTermDebt": 2762585000, "longTermDebt": 27889068000,
                         "shortLongTermDebtTotal": 2760395000, "capitalLeaseObligations": 528537000})
    assert debt["value"] == 30651653000
    assert "smaller" in debt["notes"][0]


def test_missing_debt_is_not_zero_and_conflicts_are_unavailable():
    assert resolve_debt({"shortTermDebt": 5})["value"] is None
    assert resolve_debt({"totalDebt": 10, "shortLongTermDebtTotal": 100})["value"] is None
    assert resolve_debt({"totalDebt": 0})["value"] == 0


def test_fcf_reconciliation_preserves_reported_definition_difference():
    result = statement_inputs({}, {"totalDebt": 0}, {"totalCashFromOperatingActivities": 24077,
                              "capitalExpenditures": 2677, "freeCashFlow": 21341})
    assert result["fcf"] == 21400
    assert result["provider_fcf"] == 21341
    assert result["fcf_source"] == "totalCashFromOperatingActivities - abs(capitalExpenditures)"
