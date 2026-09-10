"""Offline cross-sector regression of the canonical DCF against frozen inputs.

Never refreshes quotes, fits to prices, changes databases, or overwrites v1 evidence.
The extra ten names were locked before observing v2 results. Missing price/beta
metadata remains missing; the additional panel is an engineering holdout only.
"""
from __future__ import annotations
import gzip
import hashlib
import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from models import FinancialStatement
from services.decision_support import build_financial_context, build_company_valuation_assumptions, _valuation_inputs, calculate_valuation
from services.screener_metrics import extract_fundamental_metrics
from services.valuation_reconciliation import PACKET_KEY
from services.valuation_validation import external_comparability

OLD = ROOT / 'docs/valuation_cross_sector_2026-09-10'
OUT = ROOT / 'docs/valuation_repair_2026-09-10'


def read(path):
    return json.loads(path.read_text())


def main():
    manifest = read(OUT / 'validation_manifest.json')
    baseline = {r['ticker']: r for r in read(OLD / 'results.json')['rows']}
    betas = {r['ticker'].split('.')[0]: r for r in read(OLD / 'beta_screener.json')['response_subset']['items']}
    packets = {r['ticker'].split('.')[0]: r for r in read(OUT / 'financial_reconciliations.json')['records']}
    sectors = {ticker: sector for sector, tickers in read(OLD / 'manifest.json')['panel'].items() for ticker in tickers}
    names = list(sectors) + manifest['additional_panel']
    rows = []
    for ticker in names:
        path = ROOT / f'data/sec_mapping_cache/eodhd/{ticker}.json.gz'
        if not path.exists():
            rows.append({'ticker': ticker, 'panel': 'baseline' if ticker in baseline else 'additional',
                         'business_model_review_held': sectors.get(ticker) in {'Financials', 'Real Estate'},
                         'error': 'Frozen source cache missing'})
            continue
        raw_bytes = path.read_bytes()
        raw = json.loads(gzip.decompress(raw_bytes))
        f = raw['Financials']
        records = []
        packet_applied = False
        for end, inc in sorted(f['Income_Statement']['quarterly'].items(), reverse=True)[:8]:
            bal = dict(f['Balance_Sheet']['quarterly'].get(end, {}))
            cf = f['Cash_Flow']['quarterly'].get(end, {})
            packet = packets.get(ticker)
            if packet and packet['provider_period_end'] == end:
                bal[PACKET_KEY] = packet['reconciliation']
                packet_applied = True
            records.append(FinancialStatement(ticker=ticker + '.US', fiscal_date=date.fromisoformat(end), period='Quarterly',
                income_statement=inc, balance_sheet=bal, cash_flow=cf))
        prior = baseline.get(ticker, {})
        price_date = date.fromisoformat(prior['price_date']) if prior.get('price_date') else None
        price = prior.get('price')
        context = build_financial_context(records, share_reference_date=price_date)
        metrics = extract_fundamental_metrics(raw)
        snapshot = SimpleNamespace(market_cap=betas.get(ticker, {}).get('market_cap', metrics.get('market_cap')),
            beta_1yr=betas.get(ticker, {}).get('beta_1yr'), provider_beta=None,
            sales_growth_3yr=metrics.get('sales_growth_3yr'), sales_growth_5yr=metrics.get('sales_growth_5yr'),
            # Raw provider shares are not stamped with a quote date we don't have.
            shares_outstanding=None, date=price_date)
        basis = build_company_valuation_assumptions(context, snapshot, price)
        inputs = _valuation_inputs(context, basis)
        result = calculate_valuation(inputs, basis['default_scenarios'], price, assumption_basis=basis)
        base = result['scenarios'][1]
        a = base['assumptions']
        sector = sectors.get(ticker, raw['General'].get('Sector'))
        held = sector in {'Financials', 'Financial Services', 'Real Estate'} or ticker == 'UNH'
        checks = {}
        if result['available']:
            # Independent formula from the inputs, not the kernel's returned path.
            flow, pv = inputs['fcf'], 0
            for year in range(1, 11):
                growth = a['fcf_growth_rate'] if year <= 5 else a['fcf_growth_rate'] + (a['perpetual_growth'] - a['fcf_growth_rate']) * (year - 5) / 5
                flow *= 1 + growth
                pv += flow / (1 + a['wacc']) ** year
            terminal = flow * (1 + a['perpetual_growth']) / (a['wacc'] - a['perpetual_growth']) / (1 + a['wacc']) ** 10
            expected = (pv + terminal + inputs['cash'] - inputs['debt'] + inputs['equity_adjustment']) / inputs['shares']
            checks['independent_arithmetic'] = math.isclose(expected, base['intrinsic_value_per_share'], rel_tol=1e-10, abs_tol=1e-7)
            checks['sensitivity_center'] = math.isclose(expected, result['sensitivity']['values'][2][2], rel_tol=1e-10, abs_tol=1e-7)
            if result['implied_growth']['available']:
                checks['reverse_forward_price'] = math.isclose(result['implied_growth']['modeled_price'], price, rel_tol=1e-9)
            checks['ordered_cases'] = [v['intrinsic_value_per_share'] for v in result['scenarios']] == sorted(v['intrinsic_value_per_share'] for v in result['scenarios'])
        # Isolated debt bridge effect at the old shares; does not claim additive
        # attribution of simultaneous WACC/growth/horizon changes.
        old_debt, old_shares = prior.get('debt_dcf'), prior.get('shares_dcf')
        debt_only_delta = (old_debt - inputs['debt']) / old_shares if old_debt is not None and inputs['debt'] is not None and old_shares else None
        rows.append({'ticker': ticker, 'panel': 'baseline' if ticker in baseline else 'additional', 'sector': sector,
            'business_model_review_held': held, 'source_sha256': hashlib.sha256(raw_bytes).hexdigest(),
            'provider_updated_at': raw['General'].get('UpdatedAt'), 'historical_point_in_time': False,
            'price': price, 'price_date': prior.get('price_date'), 'available': result['available'],
            'unavailable_reasons': result['unavailable_reasons'], 'old_base': prior.get('base_value'),
            'new_base': base.get('intrinsic_value_per_share'), 'raw_equity_residual_per_share': base.get('raw_equity_residual_per_share'), 'debt_only_bridge_delta': debt_only_delta,
            'financial_correction_attached': packet_applied, 'inputs': inputs, 'assumptions': basis,
            'terminal_share': base.get('terminal_share_of_enterprise_value'), 'checks': checks})
    eligible = [r for r in rows if not r.get('business_model_review_held') and not r.get('error')]
    failed = [r['ticker'] for r in rows if (r.get('error') and not r.get('business_model_review_held')) or not all(r.get('checks', {}).values())]
    report = {'model_version': 'fcff-2.0', 'evaluated_at_utc': datetime.now(timezone.utc).isoformat(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'summary': {'sample_count': len(rows), 'evaluated_count': sum(not r.get('error') for r in rows),
            'missing_source': [r['ticker'] for r in rows if r.get('error')], 'ordinary_business_count': len(eligible),
            'available_ordinary': sum(r['available'] for r in eligible), 'failed_engineering_checks': failed,
            'historical_accuracy_validated': False, 'price_fit_used': False},
        'external_gate_example': external_comparability({'currency': 'USD', 'cash_flow_type': 'FCFF', 'terminal_method': 'perpetual_growth'},
            {'currency': 'USD', 'cash_flow_type': 'FCFE', 'terminal_method': 'exit_ps_multiple', 'source_url': 'https://www.alphaspread.com/security/nasdaq/nvda/dcf-valuation/base-case'}),
        'rows': rows}
    (OUT / 'regression_results.json').write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    print(json.dumps(report['summary'], indent=2))
    for r in rows:
        print(r['ticker'], r.get('old_base'), r.get('new_base'), r.get('unavailable_reasons', r.get('error')))
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
