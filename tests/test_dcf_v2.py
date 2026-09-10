from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest

from services.dcf import calculate_dcf_value, operating_forecast_cash_flows
from services.decision_support import DEFAULT_SCENARIOS, calculate_valuation, validate_scenarios, build_company_valuation_assumptions
from services.valuation_assumptions import estimate_company_wacc, derive_growth_scenarios
from services.valuation_reconciliation import apply_reconciliation, statement_fingerprint
from services.valuation_validation import external_comparability, select_point_in_time, score_realized_cash_flows


def wacc(beta, interest=5):
    return estimate_company_wacc(market_cap=1000, beta=beta, latest_debt=100, prior_debt=90, average_debt=95,
        interest_expense=interest, income_tax_expense=20, income_before_tax=100, current_price=10, shares=100,
        risk_free_rate=.04, equity_risk_premium=.06, fallback_debt_spread=.015, fallback_tax_rate=.21,
        assumptions_as_of="2026-09-09", valuation_date="2026-09-10")


def forecast(scale=1):
    return [dict(year=i, revenue=100 * scale, operating_margin=.20, tax_rate=.25,
        capex=40 if i <= 2 else 5, depreciation=3, change_in_working_capital=1,
        source="Fixture operating assumptions; all amounts in USD") for i in range(1, 11)]


def cases():
    return [{**s, "operating_forecast": forecast(scale), "terminal_roic": .12, "forecast_as_of": "2026-09-09"}
            for s, scale in zip(DEFAULT_SCENARIOS, (.9, 1, 1.1))]


def test_beta_zero_boundary_is_continuous_and_monotonic():
    values = [wacc(beta)["wacc"] for beta in (-2, -.01, 0, .01, .5, 1, 2, 5)]
    assert values == sorted(values)
    assert abs(wacc(-.01)["wacc"] - wacc(.01)["wacc"]) < .001
    assert wacc(-.01)["raw_beta"] == -.01
    assert wacc(0)["beta_source"] == "provided_beta"


def test_missing_interest_can_estimate_debt_cost_but_not_fabricate_cash_flow():
    missing = wacc(1, interest=None)
    assert missing["wacc"] is not None
    assert missing["interest_expense_for_fcff"] is None
    assert wacc(1, interest=90)["interest_expense_for_fcff"] == 90


def test_fcf_spike_does_not_change_revenue_growth_cases():
    inputs = dict(previous_fcf=1, current_revenue=108, previous_revenue=100, sales_growth_3yr=.1, sales_growth_5yr=.06, fallback_growth=.05)
    a, b = (derive_growth_scenarios(current_fcf=x, **inputs) for x in (1, 1000))
    assert a["base_growth"] == b["base_growth"] == pytest.approx(.08)


def test_ten_year_fade_and_terminal_cash_flow_are_reproducible():
    result = calculate_dcf_value(fcf=100, cash=50, debt=20, shares=10, fcf_growth_rate=.2, wacc=.1, perpetual_growth=.025)
    assert result["projected_growth_rates"][:5] == [.2] * 5
    assert result["projected_growth_rates"][5:] == pytest.approx([.165, .13, .095, .06, .025])
    explicit = sum(v / 1.1 ** i for i, v in enumerate(result["projected_fcf"], 1))
    terminal = result["projected_fcf"][-1] * 1.025 / .075 / 1.1 ** 10
    assert result["equity_value"] == pytest.approx(explicit + terminal + 30)
    assert result["terminal_share_of_enterprise_value"] == pytest.approx(terminal / (explicit + terminal))


def test_negative_cash_flow_forecast_funds_reinvestment_and_terminal_growth():
    path, terminal = operating_forecast_cash_flows(forecast(), .025, .1)
    assert path[:2] == [-23, -23]
    assert terminal == pytest.approx(15 * 1.025 * .75)
    value = calculate_valuation({"fcf": -50, "cash": 1, "debt": 5, "shares": 10}, cases(), 100)
    assert value["available"] is True
    assert value["implied_growth"]["available"] is False
    assert value["sensitivity"]["values"][2][2] == pytest.approx(value["scenarios"][1]["intrinsic_value_per_share"])


def test_missing_historical_cash_flow_does_not_block_sourced_operating_forecast():
    result = calculate_valuation({"fcf": None, "cash": 1, "debt": 5, "shares": 10,
        "model_input_reasons": ["Historical interest missing"], "historical_cash_flow_reasons": ["Historical interest missing"]}, cases(), 100)
    assert result["available"] is True
    assert result["unavailable_reasons"] == []


@pytest.mark.parametrize("change", ["source", "nan", "terminal", "year", "mixed", "order", "date"])
def test_operating_forecast_rejects_unsourced_or_inconsistent_assumptions(change):
    scenarios = cases()
    if change == "source": scenarios[1]["operating_forecast"][0]["source"] = ""
    if change == "nan": scenarios[1]["operating_forecast"][0]["revenue"] = float("nan")
    if change == "terminal": scenarios[1]["terminal_roic"] = .01
    if change == "year": scenarios[1]["operating_forecast"][0]["year"] = 2
    if change == "mixed": scenarios[1].pop("operating_forecast")
    if change == "order": scenarios[0]["operating_forecast"] = forecast(5)
    if change == "date": scenarios[1]["forecast_as_of"] = "2100-01-01"
    with pytest.raises(ValueError): validate_scenarios(scenarios)


def test_reconciliation_does_not_survive_source_revision():
    income, balance, cash = {"date": "2026-07-31"}, {"date": "2026-07-31", "shortTermDebt": 1}, {}
    packet = {"input_sha256": statement_fingerprint(income, balance, cash), "period_end": "2026-07-26",
        "available_at": "2026-08-26", "balance": {"totalDebt": {"value": 33, "source_url": "https://example.com/filing", "definition": "Borrowings"}}}
    attached = {**balance, "_valuation_reconciliation": packet}
    assert apply_reconciliation(income, attached, cash)[1]["totalDebt"] == 33
    revised = {**income, "new_reported_fact": 5}
    assert "totalDebt" not in apply_reconciliation(revised, attached, cash)[1]
    assert "not applied" in apply_reconciliation(revised, attached, cash)[3][0]


def test_external_price_is_never_an_accuracy_label_and_missing_dates_fail_comparability():
    result = external_comparability({"cash_flow_type": "FCFF"}, {"cash_flow_type": "FCFE", "value": 305.48})
    assert result["same_basis"] is False
    assert result["accuracy_label"] is False


def test_point_in_time_revisions_exclude_future_and_estimated_availability():
    records = [dict(ticker="ABC", period_end="2024-12-31", cash_flow_type="FCFF", available_at=d, revision=i, value=i)
               for i, d in enumerate(("2025-02-01", "2026-02-01"), 1)]
    assert select_point_in_time(records, "2025-03-01")[0]["value"] == 1
    records[0]["availability_estimated"] = True
    assert select_point_in_time(records, "2025-03-01") == []


def test_realized_scoring_retains_negative_outcomes_and_rejects_lookahead():
    prediction = dict(ticker="ABC", period_end="2025-12-31", cash_flow_type="FCFF", forecast_as_of="2024-12-31",
                      input_available_at=["2024-11-01"], bear=-10, base=0, bull=10, currency="USD", unit="USD")
    outcome = dict(ticker="ABC", period_end="2025-12-31", cash_flow_type="FCFF", available_at="2026-02-01", value=-5, currency="USD", unit="USD")
    result = score_realized_cash_flows([prediction], [outcome], "2026-09-10")
    assert result["matched_count"] == 1 and result["mean_absolute_error"] == 5
    assert result["scenario_coverage"] == 1
    prediction["input_available_at"] = ["2025-02-01"]
    assert score_realized_cash_flows([prediction], [outcome], "2026-09-10")["matched_count"] == 0


def test_default_growth_policy_does_not_expand_to_fit_high_growth_tickers():
    result = derive_growth_scenarios(current_fcf=200, previous_fcf=100, current_revenue=170,
        previous_revenue=100, sales_growth_3yr=.6, sales_growth_5yr=.5, fallback_growth=.05)
    assert result['raw_base_growth'] == .6
    assert result['base_growth'] == .2
    assert result['growth_limit_applied'] is True
    assert result['scenario_spread'] == pytest.approx(.1)


def test_equity_bridge_is_applied_once_in_forward_reverse_and_sensitivity():
    from services.valuation_inputs import statement_inputs
    from services.decision_support import calculate_implied_fcf_growth
    inc, bal, cf = {'date': '2026-06-30'}, {'date': '2026-06-30', 'totalDebt': 20, 'cash': 50}, {}
    values = {'valuationExcessCash': 30, 'valuationNonOperatingAssets': 8,
              'valuationNoncontrollingInterests': 5, 'valuationPreferredEquity': 2}
    packet = {'input_sha256': statement_fingerprint(inc, bal, cf), 'period_end': '2026-06-30', 'available_at': '2026-08-01',
        'balance': {k: {'value': v, 'source_url': 'https://example.com/reviewed-bridge', 'definition': 'Reviewed valuation basis'} for k, v in values.items()}}
    normalized = statement_inputs(inc, {**bal, '_valuation_reconciliation': packet}, cf)
    bridge = normalized['equity_bridge']
    assert bridge['complete'] and bridge['cash_added'] == 30 and bridge['equity_adjustment'] == 1
    inputs = dict(fcf=100, cash=bridge['cash_added'], debt=20, shares=10, equity_adjustment=bridge['equity_adjustment'])
    result = calculate_valuation(inputs, DEFAULT_SCENARIOS, 100)
    base = result['scenarios'][1]
    assert base['equity_value'] == pytest.approx(base['enterprise_value'] + 11)
    assert result['sensitivity']['values'][2][2] == pytest.approx(base['intrinsic_value_per_share'])
    reverse = calculate_implied_fcf_growth(inputs, base['intrinsic_value_per_share'], DEFAULT_SCENARIOS[1])
    assert reverse['implied_fcf_growth_rate'] == pytest.approx(.1)
    assert reverse['modeled_price'] == pytest.approx(base['intrinsic_value_per_share'])
    assert 'not applied' in apply_reconciliation(inc, {**bal, '_valuation_reconciliation': packet}, cf, as_of=date(2026, 7, 1))[3][0]


def test_operating_forecasts_round_trip_through_personal_api():
    from fastapi.testclient import TestClient
    from main import app
    scenarios = cases()
    with TestClient(app) as client:
        saved = client.put('/api/personal/stocks/TEST/valuation-scenarios', headers={'X-API-Key': 'test-secret'}, json={'scenarios': scenarios})
        assert saved.status_code == 200, saved.text
        read = client.get('/api/personal/stocks/TEST/valuation-scenarios', headers={'X-API-Key': 'test-secret'})
        assert read.json()['scenarios'] == scenarios
        scenarios[1]['operating_forecast'][0]['source'] = ''
        rejected = client.put('/api/personal/stocks/TEST/valuation-scenarios', headers={'X-API-Key': 'test-secret'}, json={'scenarios': scenarios})
        assert rejected.status_code == 422
        assert client.get('/api/personal/stocks/TEST/valuation-scenarios', headers={'X-API-Key': 'test-secret'}).json() == read.json()


@pytest.mark.asyncio
@pytest.mark.parametrize('fcf', [10, -10])
async def test_legacy_and_default_engine_have_identical_outputs(db_session, fcf):
    from models import Ticker, FinancialStatement, DailyPrice
    from services.analyzer import get_fundamental_valuation
    from services.decision_support import get_default_ticker_valuation
    db_session.add(Ticker(ticker='PARITY.US', name='Parity', currency='USD'))
    for month in (3, 6, 9, 12):
        end = date(2025, month, 28)
        db_session.add(FinancialStatement(ticker='PARITY.US', fiscal_date=end, period='Quarterly',
            income_statement={'totalRevenue': 100, 'incomeBeforeTax': 20, 'incomeTaxExpense': 4, 'interestExpense': 1},
            balance_sheet={'totalDebt': 20, 'cash': 10, 'commonStockSharesOutstanding': 10}, cash_flow={'freeCashFlow': fcf}))
    db_session.add(DailyPrice(ticker='PARITY.US', date=date(2026, 1, 2), close=10, adjusted_close=10))
    await db_session.commit()
    canonical = await get_default_ticker_valuation('PARITY.US', db_session)
    legacy = (await get_fundamental_valuation('PARITY.US', db_session))['valuation']
    assert legacy['model_version'] == canonical['model_version']
    assert legacy['available'] == canonical['available']
    assert legacy['dcf_intrinsic_value_per_share'] == canonical['scenarios'][1].get('intrinsic_value_per_share')
    if fcf < 0:
        assert legacy['dcf_intrinsic_value_per_share'] is None
        assert legacy['margin_of_safety'] is None


@pytest.mark.asyncio
async def test_reconciliation_import_is_atomic_and_keeps_original_facts(db_session, tmp_path, capsys):
    import json
    from models import Ticker, FinancialStatement
    from scripts.reconcile_valuation_inputs import run
    db_session.add(Ticker(ticker='IMPORT.US'))
    inc, bal, cf = {'date': '2026-06-30'}, {'date': '2026-06-30', 'shortTermDebt': 2}, {}
    row = FinancialStatement(ticker='IMPORT.US', fiscal_date=date(2026, 6, 30), period='Quarterly', income_statement=inc, balance_sheet=bal, cash_flow=cf)
    db_session.add(row)
    await db_session.commit()
    packet = {'input_sha256': statement_fingerprint(inc, bal, cf), 'period_end': '2026-06-30', 'available_at': '2026-08-01',
        'balance': {'totalDebt': {'value': 25, 'source_url': 'https://example.com/debt', 'definition': 'Borrowings'}}}
    entry = dict(ticker='IMPORT.US', period='Quarterly', provider_period_end='2026-06-30', reconciliation=packet)
    path = tmp_path / 'corrections.json'
    path.write_text(json.dumps({'records': [entry, {**entry, 'ticker': 'MISSING.US'}]}))
    with pytest.raises(ValueError):
        await run(path, True)
    await db_session.refresh(row)
    assert '_valuation_reconciliation' not in row.balance_sheet
    path.write_text(json.dumps({'records': [entry]}))
    await run(path, True)
    await db_session.refresh(row)
    assert row.balance_sheet['shortTermDebt'] == 2 and 'totalDebt' not in row.balance_sheet
    assert row.balance_sheet['_valuation_reconciliation'] == packet
    assert apply_reconciliation(row.income_statement, row.balance_sheet, row.cash_flow)[1]['totalDebt'] == 25


def test_nonpositive_equity_residual_is_diagnostic_not_a_negative_stock_price():
    result = calculate_valuation(dict(fcf=1, cash=0, debt=10000, shares=10), DEFAULT_SCENARIOS, 5)
    base = result['scenarios'][1]
    assert not result['available'] and not base['available']
    assert base['raw_equity_residual_per_share'] < 0
    assert base['intrinsic_value_per_share'] is None
    assert result['sensitivity']['values'][2][2] is None
    assert 'equity residual' in result['sensitivity']['cell_reasons'][2][2]
